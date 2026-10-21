#!/usr/bin/env bash
# inventory.sh — the upgrader's step 3 as one script (#1143).
#
# Why: the agent used to run the inventory scripts by hand and guess where each
# one writes. build_workbook.py reads <workdir>/inventory.csv, so a bare
# `module_inventory.py` call (default ./inventory.csv) missed it. This script
# gives every output an absolute path under DIR/inventory/.
#
# Usage: inventory.sh --artifacts DIR --worktree W --series S [--ssh H]
#
# Steps, each through the sibling script (path resolved from this file):
#   module_inventory   module_inventory.py W --target-version S
#                        -o DIR/inventory/inventory.csv
#   oca_check          oca_check.py W --series S --csv DIR/inventory/inventory.csv
#                        -o DIR/inventory/oca_index.json
#   studio_inventory   only with --ssh: studio_inventory.py --ssh H --artifacts DIR
#                        -> DIR/inventory/studio.csv
# A failed module_inventory skips oca_check (it needs the CSV).
# build_workbook.py reads oca_alt from oca_index.json. No oca_alt_seed.json is
# written: the index is the source.
#
# Output: one `PASS <step>` or `FAIL <step>: <why>` line per step on stderr,
# then one JSON line on stdout:
#   {ok, inventory_csv, oca_index, studio_csv|null, failures[]}
# A failure string is `<step>: exit <code>: <last stderr line>`.
#
# Test-only: ODOO_DEV_SIBLING_SCRIPTS names a directory holding stand-ins for
# module_inventory.py, oca_check.py and studio_inventory.py. Unset in real use.
#
# Exit: 0 every step passed · 10 at least one step failed · 2 usage
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS="$(cd "$SCRIPT_DIR/../.." && pwd)"
if [ -n "${ODOO_DEV_SIBLING_SCRIPTS:-}" ]; then
  MODINV="$ODOO_DEV_SIBLING_SCRIPTS/module_inventory.py"
  OCACHECK="$ODOO_DEV_SIBLING_SCRIPTS/oca_check.py"
  STUDIO="$ODOO_DEV_SIBLING_SCRIPTS/studio_inventory.py"
else
  MODINV="$SCRIPT_DIR/module_inventory.py"
  OCACHECK="$SKILLS/odoo-prior-art/scripts/oca_check.py"
  STUDIO="$SCRIPT_DIR/studio_inventory.py"
fi

usage() { echo "usage: inventory.sh --artifacts <dir> --worktree <dir> --series <series> [--ssh <host>]" >&2; exit 2; }

artifacts=""; worktree=""; series=""; ssh_host=""
while [ $# -gt 0 ]; do
  case "$1" in
    --artifacts) [ $# -ge 2 ] || usage; artifacts="$2"; shift 2 ;;
    --worktree) [ $# -ge 2 ] || usage; worktree="$2"; shift 2 ;;
    --series) [ $# -ge 2 ] || usage; series="$2"; shift 2 ;;
    --ssh) [ $# -ge 2 ] || usage; ssh_host="$2"; shift 2 ;;
    *) usage ;;
  esac
done
[ -n "$artifacts" ] && [ -n "$worktree" ] && [ -n "$series" ] || usage

mkdir -p "$artifacts/inventory" || usage
out="$(cd "$artifacts/inventory" && pwd)"
inventory_csv="$out/inventory.csv"
oca_index="$out/oca_index.json"
studio_csv="$out/studio.csv"

failures=()
errfile="$(mktemp "${TMPDIR:-/tmp}/inventory-err.XXXXXX")"
trap 'rm -f "$errfile"' EXIT

# step NAME CMD... : run, keep stderr visible, record the exit code and the
# last stderr line on failure.
step() {
  local name="$1" code last
  shift
  "$@" >&2 2>"$errfile"
  code=$?
  cat "$errfile" >&2
  if [ "$code" -eq 0 ]; then
    echo "PASS $name" >&2
  else
    last="$(grep -v '^[[:space:]]*$' "$errfile" | tail -n 1)"
    failures+=("$name: exit $code: $last")
    echo "FAIL $name: exit $code: $last" >&2
  fi
  return "$code"
}

if step module_inventory python3 "$MODINV" "$worktree" --target-version "$series" -o "$inventory_csv"; then
  step oca_check python3 "$OCACHECK" "$worktree" --series "$series" --csv "$inventory_csv" -o "$oca_index"
fi
studio_json=null
if [ -n "$ssh_host" ]; then
  step studio_inventory python3 "$STUDIO" --ssh "$ssh_host" --artifacts "$artifacts" && studio_json=1
fi

python3 -I -c 'import json,sys
a=sys.argv
print(json.dumps({"ok": not a[5:], "inventory_csv": a[1], "oca_index": a[2],
  "studio_csv": a[3] if a[4] == "1" else None, "failures": a[5:]}))' \
  "$inventory_csv" "$oca_index" "$studio_csv" "$([ "$studio_json" = 1 ] && echo 1 || echo 0)" "${failures[@]}"

[ "${#failures[@]}" -eq 0 ] || exit 10
