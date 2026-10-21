#!/usr/bin/env bash
# upgrade-preflight.sh — the upgrader's step 1 as one script with one verdict.
#
# Why: the upgrader agent used to run four checks by hand and judge each result,
# so a broken environment was sometimes "fixed" by pinning or patching a tree.
# One script runs every check, collects every failure, and prints a verdict the
# agent relays verbatim and never judges (#1149).
#
# Usage: upgrade-preflight.sh --target <series> --project "<name>"
#                             [--ssh <host>] [--repo-path <dir>]
#
# Checks, in order, each through the sibling skill script (path resolved from
# this file's location):
#   project   project-resolve.sh "<name>" -> repo, repo_path, default_branch
#             (--repo-path overrides repo_path)
#   env       preflight.sh --soft --target-series V --custom-tree <repo_path>
#             -> its failures[] are merged
#   addons    addons_paths.sh --series V -> enterprise_path; every missing[]
#             entry is a failure
#   ssh       only with --ssh: `ssh -o BatchMode=yes H true`, then
#             `psql -Atc 'select current_database()'` on H -> source_db
#
# Output: one line per check on stderr (`PASS <check>` or `FAIL <check>: <why>`),
# then one JSON line on stdout:
#   {ok, failures[], source_db, enterprise_path, repo, repo_path,
#    default_branch, env_fingerprint, env_fingerprint_parts}
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
if [ -n "${ODOO_DEV_SIBLING_SCRIPTS:-}" ]; then
  PREFLIGHT="$ODOO_DEV_SIBLING_SCRIPTS/preflight.sh"
  ADDONS="$ODOO_DEV_SIBLING_SCRIPTS/addons_paths.sh"
  RESOLVE="$ODOO_DEV_SIBLING_SCRIPTS/project-resolve.sh"
else
  PREFLIGHT="$SKILLS/odoo-task-env/scripts/preflight.sh"
  ADDONS="$SKILLS/odoo-prior-art/scripts/addons_paths.sh"
  RESOLVE="$SKILLS/odoo-repo-map/scripts/project-resolve.sh"
fi

usage() { echo "usage: upgrade-preflight.sh --target <series> --project <name> [--ssh <host>] [--repo-path <dir>]" >&2; exit 2; }

target=""; project=""; ssh_host=""; repo_path_arg=""
while [ $# -gt 0 ]; do
  case "$1" in
    --target) [ $# -ge 2 ] || usage; target="$2"; shift 2 ;;
    --project) [ $# -ge 2 ] || usage; project="$2"; shift 2 ;;
    --ssh) [ $# -ge 2 ] || usage; ssh_host="$2"; shift 2 ;;
    --repo-path) [ $# -ge 2 ] || usage; repo_path_arg="$2"; shift 2 ;;
    *) usage ;;
  esac
done
[ -n "$target" ] && [ -n "$project" ] || usage

failures=()
fail() { failures+=("$2"); echo "FAIL $1: $2" >&2; }
pass() { echo "PASS $1" >&2; }

# jq-free JSON access: field NAME of the JSON on stdin ("" when absent),
# or the list under NAME, one element per line.
jfield() { python3 -I -c 'import json,sys
v=json.load(sys.stdin).get(sys.argv[1],"")
print("" if v is None else v)' "$1" 2>/dev/null; }
jlist() { python3 -I -c 'import json,sys
for x in json.load(sys.stdin).get(sys.argv[1]) or []: print(x)' "$1" 2>/dev/null; }
lastline() { printf '%s\n' "$1" | awk 'NF{l=$0} END{print l}'; }

repo=""; repo_path=""; default_branch=""; enterprise_path=""; source_db=""; addons_path=""

# --- project ------------------------------------------------------------------
out="$(bash "$RESOLVE" "$project" 2>/dev/null)"; rc=$?
json="$(lastline "$out")"
if [ "$rc" -ne 0 ] || [ -z "$json" ]; then
  fail project "project: '$project' did not resolve (project-resolve.sh exit $rc)"
else
  repo="$(printf '%s' "$json" | jfield repo)"
  repo_path="$(printf '%s' "$json" | jfield repo_path)"
  default_branch="$(printf '%s' "$json" | jfield default_branch)"
  pass project
fi
[ -n "$repo_path_arg" ] && repo_path="$repo_path_arg"

# --- env ----------------------------------------------------------------------
if [ -z "$repo_path" ]; then
  fail env "env: no repo_path to import the custom tree from"
else
  out="$(bash "$PREFLIGHT" --soft --target-series "$target" --custom-tree "$repo_path" 2>/dev/null)"
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
printf '%s\0' "${failures[@]+"${failures[@]}"}" | python3 -I -c 'import json,sys
a=sys.argv[1:]
f=[x for x in sys.stdin.read().split("\0") if x]
print(json.dumps({"ok":a[0]=="true","failures":f,"source_db":a[1],"enterprise_path":a[2],
 "repo":a[3],"repo_path":a[4],"default_branch":a[5],"env_fingerprint":a[6],
 "env_fingerprint_parts":{"odoo_version":a[7],"enterprise_path":a[2],"enterprise_head":a[8],"addons_path":a[9]}}))' \
  "$ok" "$source_db" "$enterprise_path" "$repo" "$repo_path" "$default_branch" "$fingerprint" \
  "$odoo_ver" "$head_rev" "$addons_path"
[ "$ok" = true ] && exit 0
exit 10
