#!/usr/bin/env bash
# stack-ensure.sh — ensure the singleton project stack is running.
#
# Usage: stack-ensure.sh <repo>
#
# Rules (design doc §4):
#   - singleton per project: reuse a running <repo>-odoo-1; restart a stopped
#     one; only otherwise pay for `devcontainer up`
#   - NEVER launch against a worktree path (would mint a second compose project)
#   - below the RAM threshold, stop (not remove) the least-recently-created
#     stack that is not in ODOO_ACTIVE_REPOS (comma-separated allowlist)
#
# Two execution contexts, detected rather than configured:
#   host          docker is available -> the singleton-stack logic below
#   devcontainer  no docker, but odoo and a reachable postgres -> there is no
#                 stack to ensure, we are already inside one. Reports
#                 status "in-container" after proving odoo and postgres answer.
# The second case used to fail with a bare "docker: command not found", which
# reads as a broken script rather than "you are in the wrong place".
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
# Last stdout line: {"stack": ..., "status": "reused"|"restarted"|"created", "stopped_lru": [...]}
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/_common.sh"
MIN_FREE_GB="${MIN_FREE_GB:-6}"
READY_TIMEOUT_S="${STACK_READY_TIMEOUT_S:-60}"
LOCK_FILE="${TMPDIR:-/tmp}/odoo-task-env-stack.lock"

[ $# -eq 1 ] || { echo "usage: stack-ensure.sh <repo>" >&2; exit 2; }
repo="$1"

# A worktree path would mint a SECOND compose project for the same repo, which
# defeats the singleton this whole script exists to guarantee.
case "$repo" in
  *"${WORKTREE_SUBDIR:-.worktrees}"*|*/*) echo "refusing: '$repo' must be a bare repo name, never a worktree path" >&2; exit 2 ;;
esac

if ! command -v docker >/dev/null 2>&1; then
  # Inside the devcontainer there is no stack to ensure — this IS the stack.
  # Prove it answers, then say so; anything else here is a wrong-context error.
  if command -v odoo >/dev/null 2>&1 && pg_isready >/dev/null 2>&1; then
    echo "{\"stack\": \"$repo\", \"status\": \"in-container\", \"stopped_lru\": []}"
    exit 0
  fi
  echo "docker is not available and this is not a working Odoo container: stack-ensure.sh runs on the HOST (where the repos tree and docker live), or inside a devcontainer that already has odoo + postgres" >&2
  exit 5
fi

REPOS_DIR="${REPOS_DIR:-$("$REPO_MAP_SCRIPTS/repos-dir.sh" --raw)}"
repo_dir="$REPOS_DIR/$repo"
[ -d "$repo_dir" ] || { echo "repo not found: $repo_dir" >&2; exit 2; }

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
docker compose -f "${ODOO_SHARED_COMPOSE:-$REPOS_DIR/.devcontainer/shared/compose.yml}" up -d --wait >&2
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
  lru_json=$(printf '%s\n' "${stopped_lru[@]}" | node -e '
    const lines = require("fs").readFileSync(0, "utf8").trim().split("\n");
    console.log(JSON.stringify(lines));')
fi

echo "{\"stack\": \"$repo\", \"status\": \"$status\", \"stopped_lru\": $lru_json}"
