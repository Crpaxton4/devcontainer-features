#!/usr/bin/env bash
# upgrade-preflight.sh — the upgrader's step 1 as one script with one verdict.
#
# Why: the upgrader agent used to run four checks by hand and judge each result,
# so a broken environment was sometimes "fixed" by pinning or patching a tree.
# One script runs every check, collects every failure, and prints a verdict the
# agent relays verbatim and never judges (#1149).
#
# Usage: upgrade-preflight.sh --target <series> --project "<name>"
#                             [--ssh <host>] [--repo-root <dir>]
#
# Checks, in order, each through the sibling skill script (path resolved from
# this file's location):
#   project   project-resolve.sh "<name>" -> repo, repo_root, base_branch
#             (--repo-root overrides repo_root)
#   env       preflight.sh --soft --target-series V --custom-tree <repo_root>
#             -> its failures[] are merged
#   addons    addons_paths.sh --series V -> enterprise_path; every missing[]
#             entry is a failure
#   ssh       only with --ssh: `ssh -o BatchMode=yes H true`, then
#             `psql -Atc 'select current_database()'` on H -> source_db
#
# Output: one line per check on stderr (`PASS <check>` or `FAIL <check>: <why>`),
# then one JSON line on stdout:
#   {ok, failures[], source_db, enterprise_path, repo, repo_root,
#    base_branch, env_fingerprint, env_fingerprint_parts}
# env_fingerprint is the sha256 of `odoo --version` (or $ODOO_VERSION),
# enterprise_path, the enterprise tree's git HEAD when it is a git tree, and the
# addons path (the addons_paths list of addons_paths.sh joined with commas),
# joined by newlines. env_fingerprint_parts is {odoo_version, enterprise_path,
# enterprise_head, addons_path}: the four inputs, so a mismatch can be explained.
# Failure prefixes: `project: `, `addons: missing <x>`, `ssh: `, and the
# failure strings of preflight.sh unchanged.
#
# Test-only: ODOO_DEV_SIBLING_SCRIPTS names a directory holding stand-ins for
# preflight.sh, addons_paths.sh and project-resolve.sh. Unset in real use.
#
# Exit: 0 every check passed · 10 at least one check failed · 2 usage
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS="$(cd "$SCRIPT_DIR/../.." && pwd)"
JSON_GET="$SKILLS/../scripts/json-get.sh"
if [ -n "${ODOO_DEV_SIBLING_SCRIPTS:-}" ]; then
  PREFLIGHT="$ODOO_DEV_SIBLING_SCRIPTS/preflight.sh"
  ADDONS="$ODOO_DEV_SIBLING_SCRIPTS/addons_paths.sh"
  RESOLVE="$ODOO_DEV_SIBLING_SCRIPTS/project-resolve.sh"
else
  PREFLIGHT="$SKILLS/odoo-task-env/scripts/preflight.sh"
  ADDONS="$SKILLS/odoo-prior-art/scripts/addons_paths.sh"
  RESOLVE="$SKILLS/odoo-repo-map/scripts/project-resolve.sh"
fi

usage() { echo "usage: upgrade-preflight.sh --target <series> --project <name> [--ssh <host>] [--repo-root <dir>]" >&2; exit 2; }

target=""; project=""; ssh_host=""; repo_root_arg=""
while [ $# -gt 0 ]; do
  case "$1" in
    --target) [ $# -ge 2 ] || usage; target="$2"; shift 2 ;;
    --project) [ $# -ge 2 ] || usage; project="$2"; shift 2 ;;
    --ssh) [ $# -ge 2 ] || usage; ssh_host="$2"; shift 2 ;;
    --repo-root) [ $# -ge 2 ] || usage; repo_root_arg="$2"; shift 2 ;;
    *) usage ;;
  esac
done
[ -n "$target" ] && [ -n "$project" ] || usage

failures=()
fail() { failures+=("$2"); echo "FAIL $1: $2" >&2; }
pass() { echo "PASS $1" >&2; }

# Field NAME of the JSON on stdin ("" when absent), or the list under NAME, one
# element per line. Both go through the plugin's one JSON helper.
jfield() { "$JSON_GET" 'd[a.name]' name="$1" 2>/dev/null; }
jlist() { "$JSON_GET" -l 'd[a.name] || []' name="$1" 2>/dev/null; }
lastline() { printf '%s\n' "$1" | awk 'NF{l=$0} END{print l}'; }

repo=""; repo_root=""; base_branch=""; enterprise_path=""; source_db=""; addons_path=""

# --- project ------------------------------------------------------------------
out="$(bash "$RESOLVE" "$project" 2>/dev/null)"; rc=$?
json="$(lastline "$out")"
if [ "$rc" -ne 0 ] || [ -z "$json" ]; then
  fail project "project: '$project' did not resolve (project-resolve.sh exit $rc)"
else
  repo="$(printf '%s' "$json" | jfield repo)"
  repo_root="$(printf '%s' "$json" | jfield repo_root)"
  base_branch="$(printf '%s' "$json" | jfield base_branch)"
  pass project
fi
[ -n "$repo_root_arg" ] && repo_root="$repo_root_arg"

# --- env ----------------------------------------------------------------------
if [ -z "$repo_root" ]; then
  fail env "env: no repo_root to import the custom tree from"
else
  out="$(bash "$PREFLIGHT" --soft --target-series "$target" --custom-tree "$repo_root" 2>/dev/null)"
  json="$(lastline "$out")"
  env_failed=0
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    env_failed=1; fail env "$line"
  done < <(printf '%s' "$json" | jlist failures)
  if [ -z "$json" ]; then env_failed=1; fail env "env: preflight.sh printed no verdict"; fi
  [ "$env_failed" -eq 0 ] && pass env
fi

# --- addons -------------------------------------------------------------------
out="$(bash "$ADDONS" --series "$target" 2>/dev/null)"
json="$(lastline "$out")"
if [ -z "$json" ]; then
  fail addons "addons: addons_paths.sh printed no verdict"
else
  enterprise_path="$(printf '%s' "$json" | jfield enterprise_path)"
  addons_path="$(printf '%s' "$json" | jlist addons_paths | paste -sd, -)"
  addons_failed=0
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    addons_failed=1; fail addons "addons: missing $line"
  done < <(printf '%s' "$json" | jlist missing)
  [ "$addons_failed" -eq 0 ] && pass addons
fi

# --- ssh ----------------------------------------------------------------------
if [ -n "$ssh_host" ]; then
  if ! ssh -o BatchMode=yes "$ssh_host" true >/dev/null 2>&1; then
    fail ssh "ssh: cannot reach $ssh_host with BatchMode"
  else
    source_db="$(ssh -o BatchMode=yes "$ssh_host" psql -Atc 'select current_database()' 2>/dev/null | awk 'NF{l=$0} END{print l}')"
    if [ -z "$source_db" ]; then fail ssh "ssh: psql on $ssh_host returned no database name"; else pass ssh; fi
  fi
fi

# --- fingerprint --------------------------------------------------------------
odoo_ver="$(odoo --version 2>/dev/null | awk 'NF{l=$0} END{print l}')"
[ -n "$odoo_ver" ] || odoo_ver="${ODOO_VERSION:-}"
head_rev=""
if [ -n "$enterprise_path" ] && [ -d "$enterprise_path" ]; then
  head_rev="$(git -C "$enterprise_path" rev-parse HEAD 2>/dev/null || true)"
fi
fingerprint="$(printf '%s\n%s\n%s\n%s' "$odoo_ver" "$enterprise_path" "$head_rev" "$addons_path" | sha256sum | awk '{print $1}')"

# --- verdict ------------------------------------------------------------------
ok=true; [ "${#failures[@]}" -eq 0 ] || ok=false
"$JSON_GET" '({
  ok: a.ok === "true",
  failures: a.failures.split("\n").filter(Boolean),
  source_db: a.source_db,
  enterprise_path: a.enterprise_path,
  repo: a.repo,
  repo_root: a.repo_root,
  base_branch: a.base_branch,
  env_fingerprint: a.fingerprint,
  env_fingerprint_parts: {
    odoo_version: a.odoo_ver,
    enterprise_path: a.enterprise_path,
    enterprise_head: a.head_rev,
    addons_path: a.addons_path,
  },
})' ok="$ok" failures="$(printf '%s\n' "${failures[@]+"${failures[@]}"}")" source_db="$source_db" \
  enterprise_path="$enterprise_path" repo="$repo" repo_root="$repo_root" base_branch="$base_branch" \
  fingerprint="$fingerprint" odoo_ver="$odoo_ver" head_rev="$head_rev" addons_path="$addons_path" <<<'{}'
[ "$ok" = true ] && exit 0
exit 10
