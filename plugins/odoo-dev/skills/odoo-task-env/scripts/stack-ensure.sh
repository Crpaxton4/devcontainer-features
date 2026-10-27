#!/usr/bin/env bash
# stack-ensure.sh — ensure the singleton project stack is running.
#
# Usage: stack-ensure.sh <repo> [--target-series NN.0] [--repo-root DIR]
#
#   --target-series: the Odoo series the work targets, as in 18.0. Inside the
#                    devcontainer it is compared against the series this core
#                    actually runs; no flag means no comparison.
#   --repo-root:     the checkout of <repo>. On the host it replaces
#                    $REPOS_DIR/<repo>, so the repo itself needs no repos tree.
#                    Inside a container it is accepted and ignored.
#
# Rules (design doc §4):
#   - singleton per project: reuse a running <repo>-odoo-1; restart a stopped
#     one; only otherwise pay for `devcontainer up`
#   - NEVER launch against a worktree path (would mint a second compose project)
#   - below the RAM threshold, stop (not remove) the least-recently-created
#     stack that is not in ODOO_ACTIVE_REPOS (comma-separated allowlist)
#
# Two execution contexts, detected rather than configured, CONTAINER FIRST:
#   devcontainer  odoo on PATH and postgres answering pg_isready -> there is no
#                 stack to ensure, we are already inside one. Reports status
#                 "in-container". docker may be on PATH too: the devcontainer
#                 mounts the host socket (docker-from-docker), so docker on PATH
#                 does not say where this shell runs (#1053, as #1039 for
#                 run-tests.sh). Odoo plus postgres decide.
#   host          otherwise, when docker is available -> the singleton-stack
#                 logic below
#   neither       exit 5, "wrong place".
# The docker-first order sent a shell inside the devcontainer down the host path,
# which cannot resolve a repos tree there.
#
# Output guarantee: every exit path prints EITHER one JSON line on stdout and
# exits 0, OR an error line on stderr and exits non-zero. Nothing exits 0 without
# JSON. A caller that sees no JSON lost it in its own pipe (for example
# `| tail -1` hides the exit code): check the exit code first, use pipefail.
# Exit codes: 0 JSON printed | 1 the stack did not become usable, or a command
#   inside failed (its own stderr says which) | 2 usage, worktree path, repo or
#   repos tree not found | 5 neither a working Odoo container nor docker
#
# Concurrency: the singleton guarantee lives HERE, not in any caller. The whole
# decide/evict/create block runs under one HOST-GLOBAL exclusive flock, so
# concurrent callers cannot both see "no container" and both run `devcontainer up`
# on the same compose project (under `set -e` the loser would just die). The lock
# is host-global rather than per repo because LRU eviction stops OTHER repos'
# stacks: a per-repo lock would let N callers each evict one stack when a single
# eviction would have sufficed. Free RAM and the container listing are both
# re-sampled inside the lock, after any prior holder's work has landed. Creation
# is rare and expensive, so serializing it costs nothing.
#
# Readiness: "the container is listed by docker ps" is not "Odoo can be exec'd
# into". Every path — reused, restarted, created — waits for the shared postgres
# to accept connections FROM INSIDE the odoo container and for the odoo binary
# to answer before returning, so a "reused" verdict is never handed out for a
# stack that is still coming up.
#
# Postgres: there is no per-project db container. Every project uses the one
# shared postgres of the odoo-shared stack ($REPOS_DIR/.devcontainer/shared/
# compose.yml, container odoo-shared-db-1), which this script brings up itself
# (idempotent `up -d --wait`) so reused/restarted stacks never depend on VS Code
# having run the initializeCommand. A leftover <repo>-db-1 from the old
# per-project layout is removed on sight: it also answers to the `db` alias.
#
# Case: docker compose derives its project name by lowercasing and stripping the
# workspace folder, so repo "QOC" yields the container "qoc-odoo-1". Every
# container name here is built from the normalized $proj for that reason — using
# "$repo" verbatim made the anchored docker ps filter miss a running stack, fall
# through to `devcontainer up` for a stack that already existed, and then exec into
# a name that does not exist until READY_TIMEOUT_S expired. The allowlist is
# normalized for the same reason: it is compared against names taken off real
# containers, so a repo-map-cased entry silently protected nothing. $REPOS_DIR/$repo
# keeps the ORIGINAL case — the directory really is "QOC".
#
# Series: inside the devcontainer the probed core is REPORTED as odoo_version,
# the full last line of `odoo --version` ("Odoo Server 19.0-20260810"), because
# the caller downstream (build, then test) has no other machine-readable answer
# to "which Odoo is this". The devcontainer also ships side cores for the other
# series: side_cores lists each $ODOO_SRC_ROOT/odooNN-bin (default
# /var/lib/odoo/src) that has odooNN.conf beside it, as
# {"series","bin","conf","version"}, [] when there is none. run-tests.sh picks a
# side core by itself; this field tells a caller that the target is runnable here.
# With --target-series, a disagreement with the core's MAJOR also adds
# "series_mismatch": true and a "warning" naming both
# series. It stays a warning and still exits 0: this script ensures a stack, and
# whether the wrong stack is fatal is the caller's call — run-tests.sh refuses
# outright on the same comparison (#978).
#
# Last stdout line: {"stack": ..., "status": "reused"|"restarted"|"created"|"in-container",
#   "odoo_version": <in-container only>, "side_cores": <in-container only>,
#   "stopped_lru": [...]}
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/_common.sh"
MIN_FREE_GB="${MIN_FREE_GB:-6}"
READY_TIMEOUT_S="${STACK_READY_TIMEOUT_S:-60}"
LOCK_FILE="${TMPDIR:-/tmp}/odoo-task-env-stack.lock"

usage() { echo "usage: stack-ensure.sh <repo> [--target-series NN.0] [--repo-root DIR]" >&2; exit 2; }

target_series=""
repo_root=""
positional=()
while [ $# -gt 0 ]; do
  case "$1" in
    --target-series) [ $# -ge 2 ] || usage; target_series="$2"; shift 2 ;;
    --target-series=*) target_series="${1#*=}"; shift ;;
    --repo-root) [ $# -ge 2 ] || usage; repo_root="$2"; shift 2 ;;
    --repo-root=*) repo_root="${1#*=}"; shift ;;
    *) positional+=("$1"); shift ;;
  esac
done
[ "${#positional[@]}" -eq 1 ] || usage
repo="${positional[0]}"

# A worktree path would mint a SECOND compose project for the same repo, which
# defeats the singleton this whole script exists to guarantee.
case "$repo" in
  *"${WORKTREE_SUBDIR:-.worktrees}"*|*/*) echo "refusing: '$repo' must be a bare repo name, never a worktree path" >&2; exit 2 ;;
esac

# json_str <text> -> the text without double quotes and backslashes, so it sits
# safely between JSON quotes. Paths and version lines carry neither in practice.
json_str() { local v="$1"; printf '%s' "${v//[\"\\]/}"; }

# side_cores_json -> the side_cores array: one entry per $ODOO_SRC_ROOT/odooNN-bin
# that is executable and has odooNN.conf beside it; [] when there is none.
side_cores_json() {
  local root="${ODOO_SRC_ROOT:-/var/lib/odoo/src}" bin base num conf ver out=""
  for bin in "$root"/odoo[0-9]*-bin; do
    [ -x "$bin" ] || continue   # also skips the literal pattern of an empty glob
    base="${bin##*/}"; num="${base#odoo}"; num="${num%-bin}"
    case "$num" in ''|*[!0-9]*) continue ;; esac
    conf="$root/odoo${num}.conf"
    [ -f "$conf" ] || continue
    ver="$("$bin" -c "$conf" --version 2>/dev/null | tail -1)" || true
    out="${out:+$out, }{\"series\": \"${num}.0\", \"bin\": \"$(json_str "$bin")\", \"conf\": \"$(json_str "$conf")\", \"version\": \"$(json_str "$ver")\"}"
  done
  printf '[%s]' "$out"
}

# resolve_repos_dir -> sets REPOS_DIR, or says how to fix it and exits 2. A bare
# `set -e` death here read as "exit 1, no explanation" (#1053).
resolve_repos_dir() {
  [ -z "${REPOS_DIR:-}" ] || return 0
  REPOS_DIR="$("$REPO_MAP_SCRIPTS/repos-dir.sh" --raw)" || {
    echo "stack-ensure.sh: cannot resolve the repos tree: pass --repo-root DIR (the checkout of $repo) and set REPOS_DIR (or ODOO_SHARED_COMPOSE for the shared compose file)" >&2
    exit 2
  }
}

# Container first: a shell where odoo and pg_isready both answer IS the stack,
# whether or not docker is on PATH.
if command -v odoo >/dev/null 2>&1 && pg_isready >/dev/null 2>&1; then
  # The core this runs, stated rather than left for a caller to guess: an 18.0
  # module on a 19.0 core installs as installable=False and its test run reports
  # a green zero whose cause appears nowhere (#978). Majors are compared because
  # a core answers "19.0-20260810" and a target is "19.0".
  core_line="$(odoo --version 2>/dev/null | tail -1)" || true
  core_major="$(printf '%s' "$core_line" | { grep -oE '[0-9]+' || true; } | head -1)"
  core_series="unknown"
  [ -z "$core_major" ] || core_series="${core_major}.0"
  core_version="$(json_str "${core_line:-unknown}")"
  side_json="$(side_cores_json)"
  target_major=""
  [ -z "$target_series" ] || target_major="$(printf '%s' "$target_series" | { grep -oE '[0-9]+' || true; } | head -1)"
  if [ -n "$target_major" ] && [ "$target_major" != "$core_major" ]; then
    echo "{\"stack\": \"$repo\", \"status\": \"in-container\", \"odoo_version\": \"$core_version\", \"side_cores\": $side_json, \"series_mismatch\": true, \"warning\": \"this container runs Odoo $core_series but the work targets $target_series — nothing installed or tested here proves anything about $target_series\", \"stopped_lru\": []}"
  else
    echo "{\"stack\": \"$repo\", \"status\": \"in-container\", \"odoo_version\": \"$core_version\", \"side_cores\": $side_json, \"stopped_lru\": []}"
  fi
  exit 0
fi
if ! command -v docker >/dev/null 2>&1; then
  echo "neither a working Odoo container (odoo on PATH and pg_isready answering) nor docker is available: stack-ensure.sh runs on the HOST (where the repos tree and docker live), or inside a devcontainer that already has odoo + postgres" >&2
  exit 5
fi

# ---- host mode -----------------------------------------------------------------
if [ -n "$repo_root" ]; then
  repo_dir="$repo_root"
else
  resolve_repos_dir
  repo_dir="$REPOS_DIR/$repo"
fi
[ -d "$repo_dir" ] || { echo "repo not found: $repo_dir" >&2; exit 2; }
if [ -z "${ODOO_SHARED_COMPOSE:-}" ]; then
  resolve_repos_dir
  ODOO_SHARED_COMPOSE="$REPOS_DIR/.devcontainer/shared/compose.yml"
fi

# The one place a compose project name is derived, exactly as compose derives it.
normalize_project() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9_-]//g'
}

proj="$(normalize_project "$repo")"
[ -n "$proj" ] || { echo "repo name normalizes to nothing: $repo" >&2; exit 2; }

odoo_c="${proj}-odoo-1"
stopped_lru=()

# Normalized allowlist, built once: every entry is compared against a project name
# stripped off a real container, which is always lowercase.
IFS=',' read -r -a allowlist_entries <<< "${ODOO_ACTIVE_REPOS:-}"
active=","
for entry in "${allowlist_entries[@]}"; do
  [ -n "$entry" ] || continue
  active="${active}$(normalize_project "$entry"),"
done
active="${active}${proj},"

# ---- serialized region: everything that decides, evicts, or creates ----------
exec 9>"$LOCK_FILE"
flock 9

# Shared services first (proxy, postgres, pgweb): idempotent, and --wait blocks
# on the db healthcheck so nothing below can race initdb.
docker compose -f "$ODOO_SHARED_COMPOSE" up -d --wait >&2
# Self-heal a stale per-project db from the pre-shared layout
docker rm -f "${proj}-db-1" >/dev/null 2>&1 || true

# Re-checked INSIDE the lock: a concurrent caller may have created or started this
# very stack while we waited, in which case we reuse it instead of racing it.
if docker ps --filter "name=^${odoo_c}$" --format '{{.Names}}' | grep -q .; then
  status="reused"
elif docker ps -a --filter "name=^${odoo_c}$" --format '{{.Names}}' | grep -q .; then
  docker start "$odoo_c" >/dev/null
  status="restarted"
else
  # Re-sampled inside the lock: a prior holder's eviction has already landed, so
  # this may no longer be under pressure and N callers cannot each stop a stack.
  avail="$(free -g | awk '/^Mem:/{print $7}')"
  if [ "${avail:-0}" -lt "$MIN_FREE_GB" ]; then
    # LRU eviction: oldest running odoo stack not in the active allowlist.
    while IFS= read -r name; do
      victim="${name%-odoo-1}"
      case "$active" in *",$victim,"*) continue ;; esac
      echo "stopping LRU stack: $victim (free RAM ${avail} GB < ${MIN_FREE_GB} GB)" >&2
      docker compose -p "$victim" stop >/dev/null 2>&1 || docker stop "${victim}-odoo-1" >/dev/null 2>&1 || true
      stopped_lru+=("$victim")
      break
    done < <(docker ps --filter "name=-odoo-1" --format '{{.CreatedAt}}\t{{.Names}}' | sort | awk -F'\t' '{print $2}')
  fi
  devcontainer up --workspace-folder "$repo_dir" >&2
  status="created"
fi

flock -u 9
exec 9>&-
# ---- end serialized region ---------------------------------------------------

# Readiness probe, deliberately OUTSIDE the lock: waiting is per caller, and
# holding the global lock for it would serialize every peer behind one cold start.
ready=false
for _ in $(seq 1 "$READY_TIMEOUT_S"); do
  # pg_isready inside the odoo container uses its libpq env (PGHOST/PGUSER):
  # one probe proves the shared network AND the shared db in one go.
  if docker exec "$odoo_c" pg_isready >/dev/null 2>&1 \
     && docker exec "$odoo_c" odoo --version >/dev/null 2>&1; then
    ready=true
    break
  fi
  sleep 1
done
[ "$ready" = true ] || {
  echo "stack '$repo' (status $status) did not become usable within ${READY_TIMEOUT_S}s: need pg_isready (against odoo-shared-db-1) AND odoo --version to succeed inside '$odoo_c'" >&2
  exit 1
}

lru_json="[]"
if [ "${#stopped_lru[@]}" -gt 0 ]; then
  lru_json=$("$SCRIPT_DIR/../../../scripts/json-get.sh" 'a.lru.trim().split("\n")' \
    lru="$(printf '%s\n' "${stopped_lru[@]}")" <<<'{}')
fi

echo "{\"stack\": \"$repo\", \"status\": \"$status\", \"stopped_lru\": $lru_json}"
