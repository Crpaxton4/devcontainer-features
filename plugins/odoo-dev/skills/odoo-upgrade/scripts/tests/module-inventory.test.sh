#!/usr/bin/env bash
# module-inventory.test.sh — plant a miniature addons tree of known shape and
# assert the columns and the cell literals module_inventory.py writes, then
# feed a known set of agent JSON to build_workbook.py and assert the report and
# the CSV siblings it produces from them.
#
# Against a real customer tree the script reports whatever is there, which
# proves it parses manifests but not that a cell says what the workbook and
# every downstream consumer expect. This does the opposite: a known
# population, checked cell by cell.
#
# The join key (Module Name) and the closed literals (`none`, the origin
# tokens) are contracts: build_workbook.py and oca_check.py both key off them,
# so a silent change here is a silent miss there. The last block in this file
# asserts that contract directly, by grepping the three scripts and the two
# references against each other (#963).
#
# Stdlib python3 only — no Odoo, no network, no database. A fake `odoo`
# package is put on PYTHONPATH so "core" dependency classification is decided
# by the fixture and not by whatever happens to be installed on the machine,
# and a fake `openpyxl` (fixtures/openpyxl_stub) so the workbook build runs end
# to end without the one third-party package this repo would otherwise need.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUT="$(cd "$SCRIPT_DIR/.." && pwd)/module_inventory.py"
WORKBOOK="$(cd "$SCRIPT_DIR/.." && pwd)/build_workbook.py"
OPENPYXL_STUB="$SCRIPT_DIR/fixtures/openpyxl_stub"
work="$(mktemp -d "${TMPDIR:-/tmp}/module-inventory-test.XXXXXX")"
trap 'rm -rf "$work"' EXIT

pass=0; fail=0
expect() {
  if [ "$2" = "$3" ]; then pass=$((pass + 1)); else
    fail=$((fail + 1)); echo "FAIL $1: wanted '$3', got '$2'" >&2
  fi
}

# A report is asserted by the line it must carry, never by its whole text: the
# summary counters move whenever a column is added, the problem lines do not.
contains() {  # <label> <haystack> <needle>
  case "$2" in
    *"$3"*) pass=$((pass + 1)) ;;
    *) fail=$((fail + 1))
       echo "FAIL $1: no '$3' in:" >&2
       echo "$2" | sed 's/^/       /' >&2 ;;
  esac
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

# Every CSV in this pipeline keys on its first column — Module Name in the
# inventory, Module in the Evidence sibling — so one reader
# serves all of them.
csv_cell() {  # <csv> <first-column value> <column>
  python3 - "$1" "$2" "$3" <<'PY'
import csv, sys
with open(sys.argv[1], encoding="utf-8-sig") as fh:
    reader = csv.DictReader(fh)
    key = (reader.fieldnames or [""])[0]
    for row in reader:
        if row[key] == sys.argv[2]:
            print(row.get(sys.argv[3], "MISSING-COLUMN"))
            break
    else:
        print("MISSING-ROW")
PY
}

csv_header() {  # <csv>
  python3 - "$1" <<'PY'
import csv, sys
with open(sys.argv[1], encoding="utf-8-sig") as fh:
    print("|".join(next(csv.reader(fh))))
PY
}

cell() { csv_cell "$csv_out" "$1" "$2"; }

header="$(csv_header "$csv_out")"

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

# =============================================================================
# build_workbook.py — merging the fan-out, and what the report refuses to pass
# =============================================================================
# The workbook build is where per-agent JSON becomes the client-facing sheet.
# Everything asserted below is a contract the fan-out briefs promise and only
# this script enforces, so each case plants exactly one deviation and reads the
# report line it must produce.
#
# openpyxl is not installed here and must not be: the stub package first on
# PYTHONPATH lets the real script run end to end, and `save()` writes a JSON
# transcript instead of a spreadsheet.

SEED_HEADER="Module Name,Display Name,Module Purpose,Source version,LoC,Dependencies,Dependency origin,3rd party app?,3rd party app link,OCA repo,Complexity/risk,Upgrade action,Functional area"
SEED_ROW="acme,Acme,Extra fields on the sale order,16.0.1.0.0,120,base,core,No,,none,Low,keep,Sales"

# A work dir whose seed CSV and phase-2 file are complete and valid, so the
# only thing any case is measuring is what it writes into enrich_*.json.
wb_case() {  # <name> -> echoes the dir
  local dir="$work/wb/$1"
  mkdir -p "$dir"
  printf '%s\n%s\n' "$SEED_HEADER" "$SEED_ROW" > "$dir/inventory.csv"
  cat > "$dir/oca_alt_g1.json" <<'JSON'
[{"module": "acme", "oca_alt": "none",
  "oca_alt_evidence": "grepped acme, sale_acme across the OCA catalog"}]
JSON
  printf '%s' "$dir"
}

wb_run() {  # <dir> -> sets wb_out (stdout+stderr) and wb_status
  wb_out="$(PYTHONPATH="$OPENPYXL_STUB" python3 "$WORKBOOK" --workdir "$1" \
    --target-version 19.0 -o "$1/out.xlsx" 2>&1)"
  wb_status=$?
}

# --- #908: two files, disjoint keys, one merged record -----------------------
dir="$(wb_case merge)"
cat > "$dir/enrich_g1.json" <<'JSON'
[{"module": "acme", "native": "yes/partial"}]
JSON
cat > "$dir/enrich_g3.json" <<'JSON'
[{"module": "acme",
  "native_notes": "sale.order has carried the same field since 17.0",
  "native_evidence": "odoo/addons/sale/models/sale_order.py",
  "vendor_release": "n/a"}]
JSON
wb_run "$dir"
expect "a complete build exits 0" "$wb_status" "0"
contains "a complete build reports no problems" "$wb_out" "problems: 0"
contains "a complete build reports no sentinels" "$wb_out" "sentinels: {}"

# --- #925: verdict and description are two columns ---------------------------
expect "16 columns, verdict and notes beside each other" \
  "$(csv_header "$dir/out_inventory.csv")" \
  "Module Name|Display Name|Module Purpose|Source version|LoC|Dependencies|Dependency origin|3rd party app?|3rd party app link|OCA repo|Complexity/risk|Upgrade action|Functional area|19 native?|19 native notes|OCA 19 alternative"
expect "verdict comes from the file that set it" \
  "$(csv_cell "$dir/out_inventory.csv" acme '19 native?')" "yes/partial"
expect "notes come from the other file" \
  "$(csv_cell "$dir/out_inventory.csv" acme '19 native notes')" \
  "sale.order has carried the same field since 17.0"
expect "an evidence key set by one file alone survives the merge" \
  "$(csv_cell "$dir/out_evidence.csv" acme 'native? evidence')" \
  "odoo/addons/sale/models/sale_order.py"

# --- #908: the same key, two values, both files named ------------------------
dir="$(wb_case conflict)"
cat > "$dir/enrich_g1.json" <<'JSON'
[{"module": "acme", "native": "yes/partial"}]
JSON
cat > "$dir/enrich_g3.json" <<'JSON'
[{"module": "acme", "native": "no", "native_notes": "nothing in 19 covers it"}]
JSON
wb_run "$dir"
contains "a conflict names the module, the key and both files" "$wb_out" \
  "acme: enrich_g1.json and enrich_g3.json disagree on native"
expect "a conflict keeps the first file's value" \
  "$(csv_cell "$dir/out_inventory.csv" acme '19 native?')" "yes/partial"
expect "a key only the loser set is still merged in" \
  "$(csv_cell "$dir/out_inventory.csv" acme '19 native notes')" \
  "nothing in 19 covers it"
expect "a conflict exits non-zero" "$wb_status" "1"

# --- #1144: the legacy single-string cell is a problem, not split ------------
dir="$(wb_case legacy)"
cat > "$dir/enrich_g1.json" <<'JSON'
[{"module": "acme", "native": "yes/partial: mail composer has cc/bcc fields",
  "native_evidence": "addons/mail/wizard/mail_compose_message.py"}]
JSON
wb_run "$dir"
contains "a legacy '<verdict>: <text>' cell is a problem line" "$wb_out" \
  "acme: 19 native? is 'yes/partial: mail composer has cc/bcc fields' — expected exactly one of yes, yes/partial, partial, no"
expect "legacy cell is not split into a notes cell" \
  "$(csv_cell "$dir/out_inventory.csv" acme '19 native notes')" ""
expect "legacy cell exits non-zero" "$wb_status" "1"

# --- #925: anything outside the four verdicts is a problem -------------------
dir="$(wb_case verdict)"
cat > "$dir/enrich_g1.json" <<'JSON'
[{"module": "acme", "native": "maybe", "native_notes": "half of it"}]
JSON
wb_run "$dir"
contains "a verdict outside the vocabulary is rejected" "$wb_out" \
  "acme: 19 native? is 'maybe' — expected exactly one of yes, yes/partial, partial, no"
expect "a bad verdict exits non-zero" "$wb_status" "1"

# --- #919: the documented 300-char evidence cap is enforced ------------------
dir="$(wb_case evidence)"
python3 - "$dir/enrich_g1.json" <<'PY'
import json, sys
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    json.dump([{"module": "acme", "native": "no",
                "native_notes": "nothing in 19 covers it",
                "native_evidence": "x" * 301}], fh)
PY
wb_run "$dir"
contains "301 chars of native evidence is one char too many" "$wb_out" \
  "acme: native? evidence is 301 chars (max 300)"
expect "over-long evidence exits non-zero" "$wb_status" "1"

# --- #909: a sentinel in a delivered column is unanswered, not valid ---------
dir="$(wb_case sentinel)"
cat > "$dir/enrich_g1.json" <<'JSON'
[{"module": "acme", "purpose": "Extra fields on the sale order"}]
JSON
wb_run "$dir"
contains "a sentinel verdict is a problem line" "$wb_out" \
  "acme: 19 native? is TODO-AI"
contains "sentinels are counted per column" "$wb_out" \
  "sentinels: {'19 native?': 1}"
expect "a sentinel exits non-zero" "$wb_status" "1"

# --- #963: the OCA literal contract — one owner, asserted by grep ------------
# Three literals cross skill trees: `none`, `claimed — run oca_check.py`, and
# `already OCA: <repo>/<module>`. No shared module can hold them — there is no
# package under plugins/odoo-dev, the scripts are invoked by absolute path, and
# oca_check.py lives in another skill — so the contract is a documented owner
# (the commented OCA_NONE in oca_check.py) plus this test. `self:` was invented
# in one reference and defined nowhere; these asserts are what stops the next
# one.
SKILLS_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
OCA_CHECK="$SKILLS_DIR/odoo-prior-art/scripts/oca_check.py"
INVENTORY_MD="$SKILLS_DIR/odoo-upgrade/references/inventory.md"
METHOD_MD="$SKILLS_DIR/odoo-prior-art/references/method.md"
REVIEW_MD="$SKILLS_DIR/odoo-upgrade/references/oca-base-review.md"

for f in "$SUT" "$WORKBOOK" "$OCA_CHECK" "$INVENTORY_MD" "$METHOD_MD" \
         "$REVIEW_MD"; do
  expect "contract file exists: ${f##*/}" "$([ -f "$f" ] && echo yes)" "yes"
done

# The literal as the file actually spells it, not a count of matches: a count
# passes when the value was changed but still defined.
oca_literal() {  # <file> <constant>
  sed -n "s/^$2 = \"\(.*\)\"\$/\1/p" "$1" | head -1
}

expect "the owner declares itself" \
  "$(grep -c 'OCA literal contract' "$OCA_CHECK")" "1"
expect "both mirrors point at the owner script by name" \
  "$(grep -l 'oca_check.py' "$SUT" "$WORKBOOK" | wc -l | tr -d ' ')" "2"

owner_none="$(oca_literal "$OCA_CHECK" OCA_NONE)"
expect "oca_check.py owns OCA_NONE" "$owner_none" "none"
expect "module_inventory.py mirrors OCA_NONE" \
  "$(oca_literal "$SUT" OCA_NONE)" "$owner_none"
expect "build_workbook.py mirrors OCA_NONE" \
  "$(oca_literal "$WORKBOOK" OCA_NONE)" "$owner_none"

# OCA_UNVERIFIED is asymmetric on purpose: oca_check.py replaces the seed, it
# never writes it, so it must NOT carry a third copy to drift.
unverified="$(oca_literal "$SUT" OCA_UNVERIFIED)"
expect "module_inventory.py seeds the unverified literal" \
  "$unverified" "claimed — run oca_check.py"
expect "build_workbook.py mirrors OCA_UNVERIFIED" \
  "$(oca_literal "$WORKBOOK" OCA_UNVERIFIED)" "$unverified"
expect "oca_check.py keeps no copy of a literal it never writes" \
  "$(oca_literal "$OCA_CHECK" OCA_UNVERIFIED)" ""

# `already OCA: ` has one producer (an f-string), one validator (a regex) and
# one spec line. All three carry the prefix verbatim or the cell is rejected.
produced="$(sed -n 's/.*f"\(already OCA: \).*/\1/p' "$OCA_CHECK" | head -1)"
validated="$(sed -n 's/.*r"^\(already OCA: \).*/\1/p' "$WORKBOOK" | head -1)"
documented="$(sed -n 's/.*`\(already OCA: \)<repo>\/<module>`.*/\1/p' \
  "$INVENTORY_MD" | head -1)"
expect "oca_check.py is the sole producer of the prefix" \
  "$produced" "already OCA: "
expect "build_workbook.py validates the prefix it is sent" \
  "$validated" "$produced"
expect "inventory.md documents the prefix both sides use" \
  "$documented" "$produced"

# The #963 defect: a verdict literal named in a reference and defined nowhere.
expect "no 'self:' verdict literal in the prior-art method" \
  "$(grep -c 'self:' "$METHOD_MD")" "0"
expect "no 'self:' verdict literal in the OCA base review" \
  "$(grep -c 'self:' "$REVIEW_MD")" "0"

# ...and the row that carried it now puts the value in the column that takes
# it: `already OCA:` is an `OCA <major> alternative` value, not a `native?` one.
contains "the OCA base review row names the literal in the OCA column" \
  "$(cat "$REVIEW_MD")" '| `no` | `already OCA: <repo>/<module>` |'
contains "the prior-art decision table names it in the oca column" \
  "$(cat "$METHOD_MD")" '| `no` | `already OCA: <repo>/<module>` |'
contains "the prior-art verdict list names the literal" \
  "$(cat "$METHOD_MD")" '`already OCA: <repo>/<module>`'

echo "{\"passed\": $pass, \"failed\": $fail}"
[ "$fail" -eq 0 ]
