#!/usr/bin/env bash
# upgrade-state.test.sh — offline tests for upgrade-state.sh.
#
# A real git repo in a temp dir holds the ported modules. The artifacts dir is a path.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUT="$(cd "$SCRIPT_DIR/.." && pwd)/upgrade-state.sh"
work="$(mktemp -d "${TMPDIR:-/tmp}/upgrade-state-test.XXXXXX")"
trap 'rm -rf "$work"' EXIT

pass=0; fail=0
expect() { if [ "$2" = "$3" ]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL $1: wanted '$3', got '$2'" >&2; fi; }

A="$work/art"; mkdir -p "$A"
wt="$work/wt"; mkdir -p "$wt/a" "$wt/b"
printf "{'name': 'A', 'version': '19.0.1.0.0'}\n" > "$wt/a/__manifest__.py"
printf "{'name': 'B', 'version': '18.0.1.0.0'}\n" > "$wt/b/__manifest__.py"
git -C "$wt" init -q
git -C "$wt" add -A
git -C "$wt" -c core.hooksPath=/dev/null -c user.name=t -c user.email=t@t commit -qm port
sha="$(git -C "$wt" rev-parse HEAD)"

echo '{"from":"18.0","to":"19.0","order":["a","b"]}' > "$work/plan.json"
echo '{"ok":true,"failures":[],"env_fingerprint":"fp1"}' > "$work/pf.json"
echo '{"ok":true,"failures":[],"env_fingerprint":"fp2"}' > "$work/pf2.json"

u() { bash "$SUT" "$A" "$@"; }

u init --task 7 --from 18.0 --to 19.0 --worktree "$wt" --branch br --base-branch up \
  --plan "$work/plan.json" --preflight "$work/pf.json"
expect "init exit" "$?" 0
expect "next after init" "$(u next)" "PORT a"

u mark a port --json "{\"commit\":\"$sha\",\"manifest\":\"19.0.1.0.0\"}"
u mark a port --json "{\"commit\":\"$sha\",\"manifest\":\"19.0.1.0.0\",\"upgrade_code_commit\":{\"sha\":\"abc123\",\"diffstat\":\" a.py | 1 +\"}}"
expect "port upgrade_code_commit stored" "$(bash "$(cd "$SCRIPT_DIR/../../../.." && pwd)/scripts/artifact.sh" get "$A" upgrade | node -e 'const u=JSON.parse(require("fs").readFileSync(0,"utf8")).modules.a.port;console.log(u.upgrade_code_commit.sha+"|"+u.upgrade_code_commit.diffstat.trim())')" "abc123|a.py | 1 +"
u mark a port --json "{\"commit\":\"$sha\",\"manifest\":\"19.0.1.0.0\",\"upgrade_code_commit\":\"abc123\"}" 2>/dev/null
expect "port upgrade_code_commit must be an object" "$?" 4
u mark a status ported
expect "mark a ported" "$?" 0
expect "next after a" "$(u next)" "PORT b"

u mark a status inventoried 2>/dev/null
expect "backward move" "$?" 4

u mark b port --json "{\"commit\":\"$sha\",\"manifest\":\"18.0.1.0.0\"}"
u mark b status ported 2>/dev/null
expect "wrong manifest prefix" "$?" 4

printf "{'name': 'B', 'version': '19.0.1.0.0'}\n" > "$wt/b/__manifest__.py"
u mark b status ported
expect "mark b ported" "$?" 0

u gate workbook --problems 2 --path w.xlsx
expect "next with problems" "$(u next)" "GATE workbook"
u gate workbook --problems 0 --path w.xlsx
expect "next with no problems" "$(u next)" "INSTALL"

echo '{"status":"green","passed":true,"skipped":[]}' > "$work/sum.json"
u gate install --summary "$work/sum.json"
expect "next after install" "$(u next)" "TEST"
expect "a installed" "$(bash "$SUT" "$A" mark a status installed >/dev/null; echo $?)" 0

u build
expect "build modules" "$(bash "$(cd "$SCRIPT_DIR/../../../.." && pwd)/scripts/artifact.sh" get "$A" build | node -e 'let s="";process.stdin.on("data",d=>s+=d).on("end",()=>console.log(JSON.parse(s).modules.join(",")+" "+JSON.parse(s).odoo_version))')" "a,b 19.0"

# drop: record, git rm and commit in one transaction
git -C "$wt" config user.name t; git -C "$wt" config user.email t@t; git -C "$wt" config core.hooksPath /dev/null
mkdir -p "$wt/d"; printf "{'name': 'D', 'version': '19.0.1.0.0'}\n" > "$wt/d/__manifest__.py"
git -C "$wt" add -A; git -C "$wt" commit -qm "add d"
ART="$(cd "$SCRIPT_DIR/../../../.." && pwd)/scripts/artifact.sh"
node -e 'const {spawnSync:s}=require("child_process");const a=process.argv[1];const g=s("bash",[process.argv[2],"get",a,"upgrade"],{encoding:"utf8"});const u=JSON.parse(g.stdout);u.modules.d={status:"verdict",kind:"custom"};s("bash",[process.argv[2],"set",a,"upgrade","--json",JSON.stringify(u)])' "$A" "$ART"
u drop d --prod-uninstall --assumption A1 2>/dev/null
expect "drop without config-step" "$?" 2
u drop d --config-step "Set the standard field X on the product" --prod-uninstall --assumption A1 --question Q
expect "drop exit" "$?" 0
dget() { bash "$(cd "$SCRIPT_DIR/../../../.." && pwd)/scripts/artifact.sh" get "$A" upgrade | node -e 'let s="";process.stdin.on("data",d=>s+=d).on("end",()=>{const u=JSON.parse(s);console.log(eval(process.argv[1]))})' "$1"; }
expect "drop status" "$(dget 'u.modules.d.status')" dropped
dsha="$(dget 'u.modules.d.drop.commit')"
expect "drop commit is real" "$(git -C "$wt" rev-parse HEAD)" "$dsha"
expect "module dir gone" "$([ -e "$wt/d" ] && echo present || echo gone)" gone
expect "assumption entry" "$(dget 'u.assumptions[0].id+"|"+u.assumptions[0].question')" "A1|Q"
expect "enrich action" "$(node -e 'const e=JSON.parse(require("fs").readFileSync(process.argv[1]));console.log(e.upgrade_action+"|"+e.notes+"|"+Object.keys(e).length)' "$A/inventory/enrich_d.json")" "merge-into-standard|Set the standard field X on the product|16"

# a drop whose git rm fails: exit 4, nothing changed
node -e 'const {spawnSync:s}=require("child_process");const a=process.argv[1];const g=s("bash",[process.argv[2],"get",a,"upgrade"],{encoding:"utf8"});const u=JSON.parse(g.stdout);u.modules.c={status:"inventoried",kind:"custom"};s("bash",[process.argv[2],"set",a,"upgrade","--json",JSON.stringify(u)])' "$A" "$(cd "$SCRIPT_DIR/../../../.." && pwd)/scripts/artifact.sh"
u drop c --config-step "Use the standard option" --prod-uninstall --assumption A2 2>/dev/null
expect "failed rm exit" "$?" 4
expect "failed rm status" "$(dget 'u.modules.c.status+"|"+u.assumptions.length+"|"+(u.modules.c.drop===undefined)')" "inventoried|1|true"
expect "failed rm leaves no enrich" "$([ -e "$A/inventory/enrich_c.json" ] && echo present || echo absent)" absent

out="$(u init --task 7 --from 18.0 --to 19.0 --worktree "$wt" --branch br --base-branch up \
  --plan "$work/plan.json" --preflight "$work/pf2.json")"
expect "changed fingerprint" "$out" "STOP blocked_env"
expect "next blocked" "$(u next)" "STOP blocked_env"

# budget, kill file and fingerprint parts: each on a fresh artifacts dir
mk() { rm -rf "$work/b"; mkdir -p "$work/b"; A="$work/b"; u init --task 8 --from 18.0 --to 19.0 --worktree "$wt" --branch br --base-branch up \
  --plan "$work/plan.json" --preflight "$work/pf3.json" "$@"; }
echo '{"ok":true,"failures":[],"env_fingerprint":"fp3","env_fingerprint_parts":{"odoo_version":"19.0","enterprise_path":"/e","enterprise_head":"h","addons_path":"/a,/b"}}' > "$work/pf3.json"
echo '{"passed":false,"status":"red"}' > "$work/red.json"
echo '{"passed":true,"status":"green"}' > "$work/green.json"
bget() { bash "$(cd "$SCRIPT_DIR/../../../.." && pwd)/scripts/artifact.sh" get "$A" upgrade | node -e 'let s="";process.stdin.on("data",d=>s+=d).on("end",()=>{const u=JSON.parse(s);console.log(eval(process.argv[1]))})' "$1"; }
mk
expect "no budget: next" "$(u next)" "PORT a"
expect "budget defaults null" "$(bget 'u.run.budget.max_minutes+"|"+u.run.budget.max_install_runs')" "null|null"
expect "fingerprint parts copied" "$(bget 'u.preflight.env_fingerprint_parts.addons_path')" "/a,/b"
expect "run.updated is ISO" "$(bget '/^\d{4}-\d\d-\d\dT/.test(u.run.updated)')" true
mk --max-install-runs 1
expect "budget stored" "$(bget 'u.run.budget.max_install_runs')" 1
u gate install --summary "$work/red.json"
expect "one red install over budget" "$(u next)" "STOP budget"
mk --max-install-runs 1
u gate install --summary "$work/green.json"
expect "green install is not a budget stop" "$(u next)" "PORT a"
mk --max-minutes 0
expect "max-minutes 0 later" "$(UPGRADE_STATE_NOW=2999-01-01T00:00:00Z u next)" "STOP budget"
mk --max-minutes 60
expect "within max-minutes" "$(u next)" "PORT a"
mk
touch "$A/STOP"
expect "kill file" "$(u next)" "STOP killed"
rm -f "$A/STOP"
expect "kill file removed" "$(u next)" "PORT a"
u init --task 8 --from 18.0 --to 19.0 --worktree "$wt" --branch br --base-branch up --plan "$work/plan.json" --preflight "$work/pf3.json" --max-minutes x 2>/dev/null
expect "bad max-minutes" "$?" 2

fadd() { u friction add --tool t --expected e --observed "o" --did d "$@" 2>/dev/null; }
expect "friction list starts empty" "$(u friction list)" "[]"
fadd --class tooling
expect "friction add tooling" "$?" 0
fadd --class porting_fact --series-from 18.0 --series-to 19.0 --symptom-grep "attrs=" --fix "use invisible"
expect "friction add porting_fact" "$?" 0
expect "friction list count" "$(u friction list | node -e 'console.log(JSON.parse(require("fs").readFileSync(0,"utf8")).length)')" 2
expect "friction list porting_fact fix" "$(u friction list | node -e 'console.log(JSON.parse(require("fs").readFileSync(0,"utf8"))[1].porting_fact.fix)')" "use invisible"
expect "friction tooling porting_fact null" "$(u friction list | node -e 'console.log(JSON.parse(require("fs").readFileSync(0,"utf8"))[0].porting_fact)')" "null"
u friction add --tool t --expected e --observed "" --did d --class tooling 2>/dev/null
expect "friction empty observed" "$?" 2
fadd --class bogus
expect "friction bad class" "$?" 2
fadd --class porting_fact --series-from 18.0 --series-to 19.0 --fix f
expect "friction porting_fact missing field" "$?" 2
expect "friction list unchanged after refusals" "$(u friction list | node -e 'console.log(JSON.parse(require("fs").readFileSync(0,"utf8")).length)')" 2

echo "upgrade-state.test.sh: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
