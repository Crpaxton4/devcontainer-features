#!/usr/bin/env bash
# upgrade-tests.test.sh — offline tests for smoke_module.py and upgrade_tests.sh.
#
# smoke_module.py runs for real on a fixture tree. session-db.sh is a stub
# (ODOO_DEV_SIBLING_SCRIPTS) that prints a result JSON from a per-module file in
# $STUB_RESULTS, and upgrade-state.sh is not stubbed: with no `upgrade` section
# its `mark` calls fail, which the script reports on stderr and survives.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS="$(cd "$SCRIPT_DIR/.." && pwd)"
work="$(mktemp -d "${TMPDIR:-/tmp}/upgrade-tests-test.XXXXXX")"
trap 'rm -rf "$work"' EXIT

pass=0; fail=0
expect() { if [ "$2" = "$3" ]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL $1: wanted '$3', got '$2'" >&2; fi; }
jq_py() { python3 -I -c "import json,sys; d=json.loads(sys.argv[1]); print($2)" "$1"; }

wt="$work/wt"
mkdir -p "$wt/my_mod/tests" "$wt/my_mod/views" "$wt/my_mod/report" "$wt/plain_mod"
echo "{'name': 'my_mod', 'depends': ['sale', 'website_sale']}" > "$wt/my_mod/__manifest__.py"
echo "{'name': 'plain_mod', 'depends': ['my_mod', 'stock']}" > "$wt/plain_mod/__manifest__.py"
cat > "$wt/my_mod/views/a.xml" <<'XML'
<odoo>
  <record id="my_action" model="ir.actions.act_window"><field name="name">A</field></record>
  <record id="my_view" model="ir.ui.view"><field name="name">v</field></record>
</odoo>
XML
cat > "$wt/my_mod/report/r.xml" <<'XML'
<odoo>
  <template id="r1" inherit_id="sale.report_saleorder_document"><xpath expr="//div" position="after"/></template>
</odoo>
XML

# smoke_module.py, series 18.0.
out18="$(python3 "$SCRIPTS/smoke_module.py" --worktree "$wt" --series 18.0 --out "$work/o18")"
json18="$(printf '%s\n' "$out18" | tail -1)"
mod="$work/o18/upgrade_smoke"
expect "module dir printed" "$(printf '%s\n' "$out18" | head -1)" "$mod"
for f in __manifest__.py __init__.py tests/__init__.py tests/test_smoke.py; do
  expect "file $f" "$([ -f "$mod/$f" ] && echo yes || echo no)" yes
done
expect "http tests 18.0" "$(jq_py "$json18" 'd["http_tests"]')" 3
expect "report tests" "$(jq_py "$json18" 'd["report_tests"]')" 1
expect "test_ count" "$(grep -c '    def test_' "$mod/tests/test_smoke.py")" 4
expect "18.0 url" "$(grep -c "/odoo/action-my_mod.my_action" "$mod/tests/test_smoke.py")" 1
expect "shop test" "$(grep -c "'/shop'" "$mod/tests/test_smoke.py")" 1
expect "manifest version" "$(python3 -I -c "import ast,sys; print(ast.literal_eval(open(sys.argv[1]).read())['version'])" "$mod/__manifest__.py")" 18.0.1.0.0
expect "manifest depends" "$(python3 -I -c "import ast,sys; print(','.join(ast.literal_eval(open(sys.argv[1]).read())['depends']))" "$mod/__manifest__.py")" my_mod,plain_mod,web
python3 -m py_compile "$mod/tests/test_smoke.py"; expect "generated test compiles" "$?" 0

# Series 16.0 uses /web.
python3 "$SCRIPTS/smoke_module.py" --worktree "$wt" --series 16.0 --out "$work/o16" >/dev/null
expect "16.0 url" "$(grep -c "/web#action=my_mod.my_action" "$work/o16/upgrade_smoke/tests/test_smoke.py")" 1
python3 "$SCRIPTS/smoke_module.py" --worktree "$wt" --series 18.0 --out "$work/o2" --modules plain_mod >/dev/null
expect "--modules filter" "$(grep -c '    def test_' "$work/o2/upgrade_smoke/tests/test_smoke.py")" 1
python3 "$SCRIPTS/smoke_module.py" --worktree "$wt" >/dev/null 2>&1; expect "smoke usage exit" "$?" 2

# upgrade_tests.sh with a stub session-db.sh.
stub="$work/stub"; mkdir -p "$stub" "$work/results"
cat > "$stub/session-db.sh" <<'SH'
#!/usr/bin/env bash
m="$4"
echo "$m" >> "$STUB_CALLS"
echo "args $*" >> "$STUB_CALLS"
cat "$STUB_RESULTS/$m.json"
SH
chmod +x "$stub/session-db.sh"
export ODOO_DEV_SIBLING_SCRIPTS="$stub" STUB_RESULTS="$work/results" STUB_CALLS="$work/calls"
for m in my_mod sale website_sale stock upgrade_smoke; do
  echo '{"tests_run": 5, "failures": []}' > "$work/results/$m.json"
done
run() { bash "$SCRIPTS/upgrade_tests.sh" --repo r --task 7 --worktree "$wt" --series 18.0 --artifacts "$1" --session-id S --enterprise /ent 2>"$1.err"; }

mkdir -p "$work/a1"
o="$(run "$work/a1")"; rc=$?
expect "green exit" "$rc" 0
expect "green passed" "$(jq_py "$o" 'd["passed"]')" True
expect "total run" "$(jq_py "$o" 'd["total"]["run"]')" 25
expect "modules" "$(jq_py "$o" '",".join(sorted(d["modules"]))')" my_mod,sale,stock,upgrade_smoke,website_sale
expect "summary file" "$(jq_py "$(cat "$work/a1/tests/summary.json")" 'd["total"]["failed"]')" 0
expect "plain_mod not run" "$(grep -c '^plain_mod$' "$work/calls")" 0
expect "dep test tags" "$(grep -c -- '--test-tags /sale' "$work/calls")" 1
expect "addons path" "$(grep -c -- "--addons-path $wt,$work/a1/tests,/ent" "$work/calls")" 5

# A failure.
echo '{"tests_run": 5, "failures": ["FAIL x"]}' > "$work/results/my_mod.json"
mkdir -p "$work/a2"; o="$(run "$work/a2")"; rc=$?
expect "failure exit" "$rc" 1
expect "failure counted" "$(jq_py "$o" 'd["modules"]["my_mod"]["failed"]')" 1

# Nothing executed.
for m in my_mod sale website_sale stock upgrade_smoke; do echo '{"tests_run": 0, "failures": []}' > "$work/results/$m.json"; done
mkdir -p "$work/a3"; o="$(run "$work/a3")"; rc=$?
expect "run 0 exit" "$rc" 1
expect "run 0 passed" "$(jq_py "$o" 'd["passed"]')" False

# Usage.
bash "$SCRIPTS/upgrade_tests.sh" --repo r >/dev/null 2>&1; expect "usage exit" "$?" 2

echo "upgrade-tests.test.sh: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
