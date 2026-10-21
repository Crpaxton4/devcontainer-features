#!/usr/bin/env bash
# inventory.test.sh — offline tests for inventory.sh.
#
# The three sibling scripts are stand-ins in a stub directory named by
# ODOO_DEV_SIBLING_SCRIPTS. Each stub logs its arguments and writes the file it
# is asked for. No network, no Odoo.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUT="$(cd "$SCRIPT_DIR/.." && pwd)/inventory.sh"
work="$(mktemp -d "${TMPDIR:-/tmp}/inventory-test.XXXXXX")"
trap 'rm -rf "$work"' EXIT

pass=0; fail=0
expect() { if [ "$2" = "$3" ]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL $1: wanted '$3', got '$2'" >&2; fi; }
contains() { case "$2" in *"$3"*) expect "$1" yes yes ;; *) expect "$1" "$2" "...$3...";; esac; }

stubs="$work/stubs"; mkdir -p "$stubs"
# Each stub logs "<name> <args>" and writes the path after -o (or the studio csv).
cat > "$stubs/module_inventory.py" <<'STUB'
#!/usr/bin/env python3
import sys
open(sys.argv[0] + ".log", "a").write(" ".join(sys.argv[1:]) + "\n")
args = sys.argv[1:]
open(args[args.index("-o") + 1], "w").write("Module Name\nacme\n")
STUB
cat > "$stubs/oca_check.py" <<'STUB'
#!/usr/bin/env python3
import os, sys
open(sys.argv[0] + ".log", "a").write(" ".join(sys.argv[1:]) + "\n")
if os.environ.get("OCA_STUB_FAIL"):
    print("PARTIAL SCAN: rate limited", file=sys.stderr)
    sys.exit(2)
args = sys.argv[1:]
open(args[args.index("-o") + 1], "w").write("{}")
STUB
cat > "$stubs/studio_inventory.py" <<'STUB'
#!/usr/bin/env python3
import os, sys
open(sys.argv[0] + ".log", "a").write(" ".join(sys.argv[1:]) + "\n")
args = sys.argv[1:]
d = os.path.join(args[args.index("--artifacts") + 1], "inventory")
os.makedirs(d, exist_ok=True)
open(os.path.join(d, "studio.csv"), "w").write("x\n")
STUB
chmod +x "$stubs"/*

run() { ODOO_DEV_SIBLING_SCRIPTS="$stubs" bash "$SUT" "$@" 2>"$work/err" >"$work/out"; echo $?; }
field() { python3 -I -c 'import json,sys; print(json.dumps(json.loads(open(sys.argv[1]).read().splitlines()[-1])[sys.argv[2]]))' "$work/out" "$1"; }

art="$work/art"
# all three steps, absolute outputs
expect "green exit" "$(run --artifacts "$art" --worktree /wt --series 19.0 --ssh host1)" 0
expect "green ok" "$(field ok)" true
expect "inventory_csv" "$(field inventory_csv)" "\"$art/inventory/inventory.csv\""
expect "oca_index" "$(field oca_index)" "\"$art/inventory/oca_index.json\""
expect "studio_csv" "$(field studio_csv)" "\"$art/inventory/studio.csv\""
expect "failures empty" "$(field failures)" "[]"
expect "csv written" "$(test -s "$art/inventory/inventory.csv" && echo y)" y
expect "index written" "$(test -s "$art/inventory/oca_index.json" && echo y)" y
expect "module_inventory args" "$(cat "$stubs/module_inventory.py.log")" \
  "/wt --target-version 19.0 -o $art/inventory/inventory.csv"
expect "oca_check args" "$(cat "$stubs/oca_check.py.log")" \
  "/wt --series 19.0 --csv $art/inventory/inventory.csv -o $art/inventory/oca_index.json"
expect "studio args" "$(cat "$stubs/studio_inventory.py.log")" "--ssh host1 --artifacts $art"
contains "PASS lines" "$(cat "$work/err")" "PASS oca_check"

# no --ssh: studio_csv null, studio stub not called
rm -f "$stubs"/*.log
expect "no ssh exit" "$(run --artifacts "$work/art2" --worktree /wt --series 19.0)" 0
expect "no ssh studio_csv" "$(field studio_csv)" null
expect "studio stub not called" "$(test -e "$stubs/studio_inventory.py.log" && echo called || echo no)" no

# a failing oca_check
expect "oca fail exit" "$(OCA_STUB_FAIL=1 run --artifacts "$work/art3" --worktree /wt --series 19.0)" 10
expect "oca fail ok" "$(field ok)" false
contains "oca fail failures" "$(field failures)" "oca_check: exit 2: PARTIAL SCAN: rate limited"
contains "oca fail stderr" "$(cat "$work/err")" "FAIL oca_check"

# usage
expect "usage: no args" "$(run)" 2
expect "usage: no series" "$(run --artifacts "$work/a4" --worktree /wt)" 2
expect "usage: unknown flag" "$(run --artifacts "$work/a4" --worktree /wt --series 19.0 --x)" 2

echo "{\"passed\": $pass, \"failed\": $fail}"
[ "$fail" -eq 0 ]
