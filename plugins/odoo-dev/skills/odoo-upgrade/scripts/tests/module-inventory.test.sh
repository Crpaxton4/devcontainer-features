#!/usr/bin/env bash
# module-inventory.test.sh — plant a miniature addons tree of known shape and
# assert the columns and the cell literals module_inventory.py writes.
#
# Against a real customer tree the script reports whatever is there, which
# proves it parses manifests but not that a cell says what the workbook and
# every downstream consumer expect. This does the opposite: a known
# population, checked cell by cell.
#
# The join key (Module Name) and the closed literals (`none`, the origin
# tokens) are contracts: build_workbook.py and oca_check.py both key off them,
# so a silent change here is a silent miss there.
#
# Stdlib python3 only — no Odoo, no network, no database. A fake `odoo`
# package is put on PYTHONPATH so "core" dependency classification is decided
# by the fixture and not by whatever happens to be installed on the machine.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUT="$(cd "$SCRIPT_DIR/.." && pwd)/module_inventory.py"
work="$(mktemp -d "${TMPDIR:-/tmp}/module-inventory-test.XXXXXX")"
trap 'rm -rf "$work"' EXIT

pass=0; fail=0
expect() {
  if [ "$2" = "$3" ]; then pass=$((pass + 1)); else
    fail=$((fail + 1)); echo "FAIL $1: wanted '$3', got '$2'" >&2
  fi
}

# --- fixture: a fake core tree, so `core` classification is deterministic -----
mkdir -p "$work/pythonpath/odoo/addons/base" "$work/pythonpath/odoo/addons/sale"
: > "$work/pythonpath/odoo/__init__.py"
printf "{'name': 'Base'}\n" > "$work/pythonpath/odoo/addons/base/__manifest__.py"
printf "{'name': 'Sales'}\n" > "$work/pythonpath/odoo/addons/sale/__manifest__.py"

# --- fixture: the addons tree under inventory --------------------------------
addons="$work/addons"
mkdir -p "$addons/acme_sale_ext/models" "$addons/acme_base" \
         "$addons/vendor_portal" "$addons/dead_module" "$addons/broken_module"

cat > "$addons/acme_sale_ext/__manifest__.py" <<'MANIFEST'
{
    'name': 'Acme Sale Extension',
    'version': '16.0.1.0.0',
    'summary': 'Extra fields on the sale order',
    'author': 'Acme Integrators',
    'depends': ['base', 'sale', 'acme_base', 'ghost_module'],
    'license': 'LGPL-3',
}
MANIFEST
printf 'from . import models\n' > "$addons/acme_sale_ext/models/__init__.py"

# Manifest name equal to the technical name: Display Name still carries it.
cat > "$addons/acme_base/__manifest__.py" <<'MANIFEST'
{
    'name': 'acme_base',
    'version': '16.0.1.0.0',
    'summary': 'Shared base',
    'author': 'Odoo Community Association (OCA), Acme',
    'depends': ['base'],
}
MANIFEST

cat > "$addons/vendor_portal/__manifest__.py" <<'MANIFEST'
{
    'name': 'Vendor Portal',
    'version': '16.0.2.1.0',
    'summary': 'Bought from the store',
    'author': 'Some Vendor',
    'price': 199.0,
    'currency': 'EUR',
    'depends': [],
}
MANIFEST

cat > "$addons/dead_module/__manifest__.py" <<'MANIFEST'
{
    'name': 'Dead Module',
    'installable': False,
    'depends': [],
}
MANIFEST

printf "{'name': 'Broken',\n" > "$addons/broken_module/__manifest__.py"

# --- run ---------------------------------------------------------------------
csv_out="$work/inventory.csv"
PYTHONPATH="$work/pythonpath" ODOO_VERSION="" \
  python3 "$SUT" "$addons" --target-version 19.0 -o "$csv_out" \
  > "$work/stdout" 2> "$work/stderr"
expect "exits 0" "$?" "0"

cell() {
  python3 - "$csv_out" "$1" "$2" <<'PY'
import csv, sys
with open(sys.argv[1], encoding="utf-8-sig") as fh:
    for row in csv.DictReader(fh):
        if row["Module Name"] == sys.argv[2]:
            print(row.get(sys.argv[3], "MISSING-COLUMN"))
            break
    else:
        print("MISSING-ROW")
PY
}

header="$(python3 - "$csv_out" <<'PY'
import csv, sys
with open(sys.argv[1], encoding="utf-8-sig") as fh:
    print("|".join(next(csv.reader(fh))))
PY
)"

# --- the column contract -----------------------------------------------------
expect "13 columns, in order" "$header" \
  "Module Name|Display Name|Module Purpose|Source version|LoC|Dependencies|Dependency origin|3rd party app?|3rd party app link|OCA repo|Complexity/risk|Upgrade action|Functional area"

# --- #922: Module Name is the technical name, nothing else -------------------
expect "Module Name is technical only" "$(cell acme_sale_ext 'Module Name')" \
  "acme_sale_ext"
expect "display name in its own column" "$(cell acme_sale_ext 'Display Name')" \
  "Acme Sale Extension"
expect "display name kept when equal to technical" \
  "$(cell acme_base 'Display Name')" "acme_base"

# --- #923: Dependencies carry no markers, origins zip alongside --------------
expect "dependency names only" "$(cell acme_sale_ext 'Dependencies')" \
  "base; sale; acme_base; ghost_module"
expect "origins, same order and separator" \
  "$(cell acme_sale_ext 'Dependency origin')" "core; core; C; ?"
expect "no dependencies -> no names" "$(cell vendor_portal 'Dependencies')" ""
expect "no dependencies -> no origins" \
  "$(cell vendor_portal 'Dependency origin')" ""

# The enterprise token cannot be produced from a fixture (that tree is a fixed
# devcontainer path), so the whole vocabulary is asserted on the function.
origins="$(python3 - "$SUT" <<'PY'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("mi", sys.argv[1])
mi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mi)
print(mi.classify_deps(["a", "b", "c", "d"], {"a"}, {"b"}, {"c"})[1])
PY
)"
expect "origin vocabulary" "$origins" "core; E; C; ?"

# --- #924: `none` is the literal for "no OCA repo", never prose --------------
expect "no OCA claim -> none" "$(cell acme_sale_ext 'OCA repo')" "none"
expect "OCA claim -> run the checker" "$(cell acme_base 'OCA repo')" \
  "claimed — run oca_check.py"

# --- the rest of the seed still holds ----------------------------------------
expect "priced module is 3rd party" "$(cell vendor_portal '3rd party app?')" \
  "Yes"
expect "store link for priced module" \
  "$(cell vendor_portal '3rd party app link')" \
  "https://apps.odoo.com/apps/modules/19.0/vendor_portal"
expect "installable False -> drop?" "$(cell dead_module 'Upgrade action')" \
  "drop?"
expect "functional area is left to the AI" \
  "$(cell acme_sale_ext 'Functional area')" "TODO-AI"
expect "unparseable manifest is a row, not a crash" \
  "$(cell broken_module 'Module Name')" "broken_module"
case "$(cell broken_module 'Module Purpose')" in
  TODO-AI*) pass=$((pass + 1)) ;;
  *) fail=$((fail + 1))
     echo "FAIL broken manifest purpose: $(cell broken_module 'Module Purpose')" >&2 ;;
esac

echo "{\"passed\": $pass, \"failed\": $fail}"
[ "$fail" -eq 0 ]
