#!/usr/bin/env bash
# port-plan.test.sh — offline tests for port-plan.py.
#
# A fixture tree of three modules in one dependency chain (c -> b -> a) with one
# pattern hit, and a second tree with a dependency cycle. Stdlib python3 only.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUT="$(cd "$SCRIPT_DIR/.." && pwd)/port-plan.py"
work="$(mktemp -d "${TMPDIR:-/tmp}/port-plan-test.XXXXXX")"
trap 'rm -rf "$work"' EXIT

pass=0; fail=0
expect() { if [ "$2" = "$3" ]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL $1: wanted '$3', got '$2'" >&2; fi; }
contains() { case "$2" in *"$3"*) expect "$1" yes yes ;; *) expect "$1" "$2" "...$3...";; esac; }

# module <tree> <name> <depends list as python text>
module() {
  mkdir -p "$1/$2"
  printf "{'name': '%s', 'version': '16.0.1.0.0', 'depends': [%s]}\n" "$2" "$3" > "$1/$2/__manifest__.py"
}

tree="$work/tree"
# Names sort the other way round to the dependency order, so the sort alone cannot pass.
module "$tree" zeta_base "'base'"
module "$tree" mid "'zeta_base'"
module "$tree" alpha_top "'mid', 'sale'"
printf "f = fields.Char(states={'draft': [('readonly', False)]})\n" > "$tree/zeta_base/model.py"
printf "x = 1\n" > "$tree/mid/model.py"

out="$(python3 "$SUT" --from 16.0 --to 17.0 --worktree "$tree" --artifacts "$work/art" 2>&1)"; status=$?
expect "plan exits 0" "$status" "0"
contains "the summary names the counts" "$out" "3 modules"
summary="$(python3 - "$work/art/plan.json" <<'PY'
import json, sys
p = json.load(open(sys.argv[1]))
hits = [(i["module"], h["series"], h["file"], h["line"]) for i in p["items"] for h in i["hits"]]
print(p["from"], p["to"], ",".join(p["hops"]), ",".join(p["order"]), hits)
PY
)"
expect "plan.json carries hops, order and the one hit" "$summary" \
  "16.0 17.0 17.0 zeta_base,mid,alpha_top [('zeta_base', '17.0', 'model.py', 1)]"

# Hops over several series, and a hit from a later series only when it is a hop.
python3 "$SUT" --from 16.0 --to 19.0 --worktree "$tree" --artifacts "$work/art2" >/dev/null 2>&1
hops="$(python3 -c 'import json,sys; print(",".join(json.load(open(sys.argv[1]))["hops"]))' "$work/art2/plan.json")"
expect "16.0 to 19.0 has three hops" "$hops" "17.0,18.0,19.0"

# A cycle names itself and exits 2.
cyc="$work/cycle"
module "$cyc" cyc_a "'cyc_b'"
module "$cyc" cyc_b "'cyc_a'"
out="$(python3 "$SUT" --from 16.0 --to 17.0 --worktree "$cyc" --artifacts "$work/art3" 2>&1)"; status=$?
expect "a cycle exits 2" "$status" "2"
contains "the cycle is named" "$out" "cyc_a -> cyc_b -> cyc_a"

# Usage errors exit 2.
python3 "$SUT" --from 17.0 --to 16.0 --worktree "$tree" --artifacts "$work/art4" >/dev/null 2>&1
expect "from later than to exits 2" "$?" "2"
python3 "$SUT" --from 16.0 >/dev/null 2>&1
expect "missing arguments exit 2" "$?" "2"

echo "{\"passed\": $pass, \"failed\": $fail}"
[ "$fail" -eq 0 ]
