#!/usr/bin/env bash
# upgrade_tests.sh — the upgrade run's test stage, with measured evidence.
#
# Why: a green install proves little when the custom modules have no tests. This
# script generates the smoke module (smoke_module.py), then runs the tests of
# every ported module that has a tests/ dir, the smoke module, and the standard
# tests of the direct core dependencies of the custom tree. It writes the counts
# into the upgrade state. A run that executed nothing is failed, never done (#1165).
#
# Usage: upgrade_tests.sh --repo <repo> --task <id> --worktree W --series T
#                         --artifacts A --session-id S [--enterprise DIR]
#
# Each run is `session-db.sh run <repo> <id> <module> --session-id S
#   --addons-path W,A/tests[,DIR] --with-tours --put A/tests`
# (dependencies add `--test-tags /<dep>`). The result JSON of each run is kept as
# A/tests/<module>.json.
# Writes A/tests/summary.json:
#   {modules: {<m>: {run, failed, passed}}, total: {run, failed}, passed}
# total.run == 0 -> passed: false. Prints that JSON on one line.
# State: `upgrade-state.sh A mark <m> tests --json '{run, failed}'` for each tree
# module, and `mark <m> status tested` when its run passed. A tree module without
# a tests/ dir is covered by the smoke module: it is marked run 0 and tested when
# the smoke module passed.
#
# Test-only: ODOO_DEV_SIBLING_SCRIPTS names a directory holding a stand-in for
# session-db.sh. Unset in real use.
#
# Exit: 0 passed · 1 failed · 2 usage
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS="$(cd "$SCRIPT_DIR/../.." && pwd)"
if [ -n "${ODOO_DEV_SIBLING_SCRIPTS:-}" ]; then
  SESSION_DB="$ODOO_DEV_SIBLING_SCRIPTS/session-db.sh"
else
  SESSION_DB="$SKILLS/odoo-session-db/scripts/session-db.sh"
fi

usage() { echo "usage: upgrade_tests.sh --repo <repo> --task <id> --worktree W --series T --artifacts A --session-id S [--enterprise DIR]" >&2; exit 2; }

repo=""; task=""; wt=""; series=""; art=""; sid=""; ent=""
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) [ $# -ge 2 ] || usage; repo="$2"; shift 2 ;;
    --task) [ $# -ge 2 ] || usage; task="$2"; shift 2 ;;
    --worktree) [ $# -ge 2 ] || usage; wt="$2"; shift 2 ;;
    --series) [ $# -ge 2 ] || usage; series="$2"; shift 2 ;;
    --artifacts) [ $# -ge 2 ] || usage; art="$2"; shift 2 ;;
    --session-id) [ $# -ge 2 ] || usage; sid="$2"; shift 2 ;;
    --enterprise) [ $# -ge 2 ] || usage; ent="$2"; shift 2 ;;
    *) usage ;;
  esac
done
for v in repo task wt series art sid; do [ -n "${!v}" ] || usage; done
[ -d "$wt" ] || { echo "upgrade_tests.sh: no worktree $wt" >&2; exit 2; }
command -v python3 >/dev/null 2>&1 || { echo "upgrade_tests.sh: python3 is required" >&2; exit 2; }

tests_dir="$art/tests"
mkdir -p "$tests_dir"
python3 "$SCRIPT_DIR/smoke_module.py" --worktree "$wt" --series "$series" --out "$tests_dir" >&2 \
  || { echo "upgrade_tests.sh: smoke_module.py failed" >&2; exit 2; }

addons="$wt,$tests_dir"
[ -z "$ent" ] || addons="$addons,$ent"

# Tree modules (with a flag for a tests/ dir) and the direct core dependencies.
plan="$(python3 -I - "$wt" <<'PY'
import ast, os, sys
wt = sys.argv[1]
tree, deps = {}, set()
for e in sorted(os.listdir(wt)):
    p = os.path.join(wt, e)
    try:
        d = ast.literal_eval(open(os.path.join(p, "__manifest__.py"), encoding="utf-8").read())
    except (OSError, ValueError, SyntaxError):
        continue
    tree[e] = os.path.isdir(os.path.join(p, "tests"))
    deps.update(d.get("depends", []))
for m, has in tree.items():
    print("tree", m, int(has))
for d in sorted(deps - set(tree)):
    print("dep", d, 0)
PY
)"

results="$tests_dir/results.tsv"
: > "$results"

run_one() { # module [extra run-tests args...]
  local m="$1" out rc=0 line
  shift
  out="$("$SESSION_DB" run "$repo" "$task" "$m" --session-id "$sid" \
    --addons-path "$addons" --with-tours --put "$tests_dir" "$@")" || rc=$?
  line="$(printf '%s\n' "$out" | python3 -I -c '
import json, sys
res = None
for l in sys.stdin:
    try:
        j = json.loads(l)
    except ValueError:
        continue
    if isinstance(j, dict):
        res = j
res = res or {}
print(json.dumps(res))' )"
  printf '%s\n' "$line" > "$tests_dir/$m.json"
  printf '%s\t%s\n' "$m" "$rc" >> "$results"
}

while read -r kind m has; do
  [ -n "$kind" ] || continue
  if [ "$kind" = tree ] && [ "$has" = 1 ]; then run_one "$m"; fi
  if [ "$kind" = dep ]; then run_one "$m" --test-tags "/$m"; fi
done <<<"$plan"
run_one upgrade_smoke

summary="$(python3 -I - "$tests_dir" "$plan" <<'PY'
import json, os, sys
d, plan = sys.argv[1], sys.argv[2]
rc = dict(l.split("\t") for l in open(os.path.join(d, "results.tsv")).read().splitlines() if l)
mods = {}
for m in rc:
    try:
        r = json.load(open(os.path.join(d, m + ".json")))
    except (OSError, ValueError):
        r = {}
    run = int(r.get("tests_run") or 0)
    failed = len(r.get("failures") or [])
    if rc[m] != "0" and failed == 0:
        failed = 1
    mods[m] = {"run": run, "failed": failed, "passed": run > 0 and failed == 0}
total = {"run": sum(v["run"] for v in mods.values()), "failed": sum(v["failed"] for v in mods.values())}
out = {"modules": mods, "total": total, "passed": total["run"] > 0 and total["failed"] == 0}
open(os.path.join(d, "summary.json"), "w").write(json.dumps(out) + "\n")
print(json.dumps(out))
PY
)"
printf '%s\n' "$summary"

# Record the counts per tree module.
smoke_ok="$(python3 -I -c 'import json,sys; print(int(json.loads(sys.argv[1])["modules"]["upgrade_smoke"]["passed"]))' "$summary")"
STATE="$SCRIPT_DIR/upgrade-state.sh"
while read -r kind m has; do
  [ "$kind" = tree ] || continue
  if [ "$has" = 1 ]; then
    read -r run failed ok < <(python3 -I -c 'import json,sys; v=json.loads(sys.argv[1])["modules"][sys.argv[2]]; print(v["run"], v["failed"], int(v["passed"]))' "$summary" "$m")
  else
    run=0; failed=0; ok="$smoke_ok"
  fi
  "$STATE" "$art" mark "$m" tests --json "{\"run\": $run, \"failed\": $failed}" >&2 || echo "upgrade_tests.sh: could not mark tests for $m" >&2
  if [ "$ok" = 1 ]; then st=tested; else st=failed; fi
  "$STATE" "$art" mark "$m" status "$st" >&2 || echo "upgrade_tests.sh: could not mark status $st for $m" >&2
done <<<"$plan"

passed="$(python3 -I -c 'import json,sys; print(int(json.loads(sys.argv[1])["passed"]))' "$summary")"
[ "$passed" = 1 ] && exit 0
exit 1
