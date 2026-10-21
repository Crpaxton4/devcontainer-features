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

out="$(u init --task 7 --from 18.0 --to 19.0 --worktree "$wt" --branch br --base-branch up \
  --plan "$work/plan.json" --preflight "$work/pf2.json")"
expect "changed fingerprint" "$out" "STOP blocked_env"
expect "next blocked" "$(u next)" "STOP blocked_env"

echo "upgrade-state.test.sh: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
