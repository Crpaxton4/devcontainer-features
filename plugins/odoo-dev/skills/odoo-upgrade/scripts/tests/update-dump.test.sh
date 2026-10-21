#!/usr/bin/env bash
# update-dump.test.sh — offline tests for update_dump.sh, the diff parser of
# post-update-check.py and the UPDATE stage of upgrade-state.sh (#1163).
#
# Stub `psql` and `odoo` on PATH. The stub psql answers only the neutralize query;
# the stub odoo accepts `-u`, logs like install_all's stub and fails on request.
# The SQL half of post-update-check.py needs a real Postgres and is not tested here.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS="$(cd "$SCRIPT_DIR/.." && pwd)"
work="$(mktemp -d "${TMPDIR:-/tmp}/update-dump-test.XXXXXX")"
trap 'rm -rf "$work"' EXIT

pass=0; fail=0
expect() { if [ "$2" = "$3" ]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL $1: wanted '$3', got '$2'" >&2; fi; }
jq_() { node -e 'const j=JSON.parse(require("fs").readFileSync(0,"utf8"));console.log(String(eval(process.argv[1])))' "$1"; }

bin="$work/bin"; mkdir -p "$bin"; export STUB_STATE="$work/state"; mkdir -p "$STUB_STATE"
cat > "$bin/psql" <<'STUB'
#!/usr/bin/env bash
sql=""; db=""; prev=""
for arg in "$@"; do
  case "$prev" in -Atqc) sql="$arg" ;; -d) db="$arg" ;; esac
  prev="$arg"
done
case "$sql" in
  *database.is_neutralized*) cat "$STUB_STATE/neutral.$db" 2>/dev/null ;;
esac
exit 0
STUB
cat > "$bin/odoo" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$STUB_STATE/odoo.argv"
db=""; mods=""; prev=""
for arg in "$@"; do
  case "$prev" in -d) db="$arg" ;; -u) mods="$arg" ;; esac
  prev="$arg"
done
echo "2026-09-25 10:00:00,000 1 INFO $db odoo.modules.loading: loading 1 modules..."
if [ -n "${ODOO_WARN:-}" ]; then
  echo "2026-09-25 10:00:00,600 1 WARNING $db odoo.tools.view_validation: attribute 'group'  is not valid.  Did you mean 'groups'?"
  echo "2026-09-25 10:00:00,700 1 WARNING $db odoo.modules.module: Missing \`author\` key in manifest for 'alpha', defaulting to 'False'"
fi
if [ "$mods" = "${ODOO_FAIL_ON:-}" ]; then
  echo "Traceback (most recent call last):"
  frame="$ODOO_TREE/alpha/models/alpha.py"
  if [ -n "${ODOO_FRAME_ENV:-}" ]; then frame="/usr/lib/python3/dist-packages/odoo/models.py"; fi
  echo "  File \"$frame\", line 1, in _compute"
  echo "ValueError: boom in $mods"
  exit 1
fi
echo "2026-09-25 10:00:09,000 1 INFO $db odoo.modules.loading: Modules loaded."
exit 0
STUB
chmod +x "$bin"/*
export PATH="$bin:$PATH"

tree="$work/tree"; mkdir -p "$tree/alpha"; export ODOO_TREE="$tree"
art="$work/art"; mkdir -p "$art"
printf 'true\n' > "$STUB_STATE/neutral.dump_ok"
printf 'false\n' > "$STUB_STATE/neutral.dump_raw"
run() { # run <db> [extra args...]; fills rc and out
  local db="$1"; shift
  out="$(bash "$SCRIPTS/update_dump.sh" --db "$db" --worktree "$tree" --series 19.0 --artifacts "$art" \
    --session-id s1 --modules alpha,beta --summary "$work/sum.json" "$@" 2>/dev/null)"; rc=$?
}

# ---- the neutralize gate
: > "$STUB_STATE/odoo.argv"
run dump_raw
expect "not neutralized: exit" "$rc" 10
expect "not neutralized: status" "$(jq_ 'j.status' < "$work/sum.json")" env_failure
expect "not neutralized: odoo never ran" "$(wc -l < "$STUB_STATE/odoo.argv" | tr -d ' ')" 0
run dump_missing
expect "unknown database: exit" "$rc" 10

# ---- a green run: two runs, in order, on the given database
run dump_ok --enterprise /ent
expect "green: exit" "$rc" 0
expect "green: status" "$(jq_ 'j.status+"|"+j.passed' <<< "$out")" "pass|true"
expect "green: steps" "$(jq_ 'j.runs.map(r=>r.step+":"+r.rc).join(",")' <<< "$out")" "ported:0,all:0"
expect "green: modules" "$(jq_ 'j.modules.join(",")' <<< "$out")" "alpha,beta"
expect "green: summary file equals stdout" "$(jq_ 'j.db' < "$work/sum.json")" dump_ok
expect "green: first call" "$(sed -n 1p "$STUB_STATE/odoo.argv" | cut -d' ' -f1-4)" "-d dump_ok -u alpha,beta"
expect "green: second call" "$(sed -n 2p "$STUB_STATE/odoo.argv" | cut -d' ' -f1-4)" "-d dump_ok -u all"
expect "green: enterprise on the path" "$(grep -c -- "--addons-path=$tree,/ent" "$STUB_STATE/odoo.argv")" 2
expect "green: data dir" "$(grep -c -- "--data-dir $art/update/data" "$STUB_STATE/odoo.argv")" 2

# ---- the warning gate
export ODOO_WARN=1
run dump_ok
expect "new warning: exit" "$rc" 1
expect "new warning: status" "$(jq_ 'j.status' <<< "$out")" failed
expect "new warning: listed" "$(jq_ 'j.warnings_new.length+":"+j.warnings_new[0]' <<< "$out" | cut -c1-40)" "1:odoo.tools.view_validation: attribute "
expect "new warning: stops after the first run" "$(jq_ 'j.runs.length' <<< "$out")" 1
printf 'view_validation\n' > "$art/accepted_warnings.txt"
run dump_ok
expect "accepted warning: exit" "$rc" 0
rm -f "$art/accepted_warnings.txt"; unset ODOO_WARN

# ---- failures and their class
export ODOO_FAIL_ON=all
run dump_ok
expect "all fails: exit" "$rc" 1
expect "all fails: class" "$(jq_ 'j.runs[1].step+":"+j.runs[1].class' <<< "$out")" "all:port"
expect "all fails: decisive" "$(jq_ 'j.runs[1].decisive' <<< "$out")" "ValueError: boom in all"
export ODOO_FRAME_ENV=1
run dump_ok
expect "frame outside the worktree: exit" "$rc" 10
expect "frame outside the worktree: status" "$(jq_ 'j.status' <<< "$out")" env_failure
unset ODOO_FAIL_ON ODOO_FRAME_ENV

# ---- usage
bash "$SCRIPTS/update_dump.sh" --db x >/dev/null 2>&1
expect "missing arguments: exit" "$?" 2

# ---- post-update-check.py: the diff parser, with no database
repo="$work/repo"; mkdir -p "$repo/alpha/models"
cat > "$repo/alpha/models/foo.py" <<'PY'
from odoo import api, fields, models


class Foo(models.Model):
    _name = 'x.foo'

    old_field = fields.Char()
    total = fields.Float(compute='_compute_total', store=True)
    shown = fields.Float(compute='_compute_shown')

    @api.depends('line_ids')
    def _compute_total(self):
        pass

    @api.depends('line_ids')
    def _compute_shown(self):
        pass
PY
git -C "$repo" init -q -b main
git -C "$repo" add -A
git -C "$repo" -c core.hooksPath=/dev/null -c user.name=t -c user.email=t@t commit -qm base
git -C "$repo" branch src
cat > "$repo/alpha/models/foo.py" <<'PY'
from odoo import api, fields, models


class Foo(models.Model):
    _name = 'x.foo'

    total = fields.Float(compute='_compute_total', store=True)
    shown = fields.Float(compute='_compute_shown')

    @api.depends('line_ids', 'line_ids.qty')
    def _compute_total(self):
        pass

    @api.depends('line_ids', 'line_ids.qty')
    def _compute_shown(self):
        pass
PY
git -C "$repo" -c core.hooksPath=/dev/null -c user.name=t -c user.email=t@t commit -qam port
dry="$(python3 "$SCRIPTS/post-update-check.py" --worktree "$repo" --base src --dry-run)"; rc=$?
expect "dry-run: exit" "$rc" 0
expect "dry-run: only the stored computed field" "$(jq_ 'j.recompute.map(r=>r.model+"."+r.field).join(",")' <<< "$dry")" "x.foo.total"
expect "dry-run: removed field" "$(jq_ 'j.removed_names.join(",")' <<< "$dry")" "old_field"
python3 "$SCRIPTS/post-update-check.py" --worktree "$repo" --base nosuchref --dry-run >/dev/null 2>&1
expect "dry-run: bad ref is exit 2" "$?" 2
python3 "$SCRIPTS/post-update-check.py" --worktree "$repo" --base src >/dev/null 2>&1
expect "no db and no out is exit 2" "$?" 2

# ---- upgrade-state.sh: the UPDATE stage
A="$work/sart"; mkdir -p "$A"
echo '{"from":"18.0","to":"19.0","order":[]}' > "$work/plan.json"
echo '{"ok":true,"failures":[],"env_fingerprint":"fp1"}' > "$work/pf.json"
u() { bash "$SCRIPTS/upgrade-state.sh" "$A" "$@"; }
u init --task 7 --from 18.0 --to 19.0 --worktree "$repo" --branch br --base-branch up \
  --plan "$work/plan.json" --preflight "$work/pf.json" --upgraded-dump dump_ok
expect "init with upgraded dump" "$?" 0
u gate workbook --problems 0 --path w.xlsx
echo '{"status":"pass","passed":true,"skipped":[]}' > "$work/inst.json"
u gate install --summary "$work/inst.json"
expect "next: UPDATE after TEST" "$(u next)" UPDATE
echo '{"recompute":[{"model":"x.foo","field":"total","null_rows":3}],"hits":[]}' > "$work/dirty.json"
echo '{"recompute":[{"model":"x.foo","field":"total","null_rows":0}],"hits":[]}' > "$work/clean.json"
echo '{"status":"pass","passed":true}' > "$work/upd.json"
u gate update --summary "$work/upd.json" --check "$work/dirty.json"
expect "next: still UPDATE on NULL rows" "$(u next)" UPDATE
u gate update --summary "$work/upd.json" --check "$work/clean.json"
expect "next: PR after a green update" "$(u next)" PR
expect "update record" "$(bash "$SCRIPTS/../../../scripts/artifact.sh" get "$A" upgrade | jq_ 'j.update.runs+":"+j.update.last.green')" "2:true"
B="$work/sart2"; mkdir -p "$B"
bash "$SCRIPTS/upgrade-state.sh" "$B" init --task 8 --from 18.0 --to 19.0 --worktree "$repo" --branch br \
  --base-branch up --plan "$work/plan.json" --preflight "$work/pf.json"
bash "$SCRIPTS/upgrade-state.sh" "$B" gate workbook --problems 0 --path w.xlsx
bash "$SCRIPTS/upgrade-state.sh" "$B" gate install --summary "$work/inst.json"
expect "next: no dump, straight to PR" "$(bash "$SCRIPTS/upgrade-state.sh" "$B" next)" PR
expect "no dump: upgraded_dump null" "$(bash "$SCRIPTS/../../../scripts/artifact.sh" get "$B" upgrade | jq_ 'j.inputs.upgraded_dump')" null

echo "update-dump.test.sh: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
