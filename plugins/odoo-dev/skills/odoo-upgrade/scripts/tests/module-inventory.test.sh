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
# build_workbook.py — one strict record per module, and what the report refuses
# =============================================================================
# The workbook build is where per-module JSON becomes the client-facing sheet.
# Everything asserted below is a contract the enrichment brief promises and only
# this script enforces, so each case plants exactly one deviation and reads the
# report line it must produce.
#
# openpyxl is not installed here and must not be: the stub package first on
# PYTHONPATH lets the real script run end to end, and `save()` writes a JSON
# transcript instead of a spreadsheet.

SEED_HEADER="Module Name,Display Name,Module Purpose,Source version,LoC,Dependencies,Dependency origin,3rd party app?,3rd party app link,OCA repo,Complexity/risk,Upgrade action,Functional area"
SEED_ROW="acme,Acme,Extra fields on the sale order,16.0.1.0.0,120,base,core,No,,none,Low,keep,Sales"

# A work dir holding only the seed CSV; a case adds enrich_acme.json itself.
wb_case() {  # <name> -> echoes the dir
  local dir="$work/wb/$1"
  mkdir -p "$dir"
  printf '%s\n%s\n' "$SEED_HEADER" "$SEED_ROW" > "$dir/inventory.csv"
  printf '%s' "$dir"
}

# Write a complete, valid enrich_acme.json, then apply the case's deviation:
# <overrides-json> sets keys, <drop,keys> removes them.
wb_record() {  # <dir> [overrides-json] [comma-separated keys to drop]
  local overrides='{}'
  python3 - "$1/enrich_acme.json" "${2:-$overrides}" "${3:-}" <<'PY'
import json, sys
record = {
    "module": "acme", "purpose": "Extra fields on the sale order",
    "third_party": "No", "third_party_link": "", "complexity": "Low",
    "upgrade_action": "merge-into-standard", "functional_area": "Sales",
    "native": "yes/partial",
    "native_notes": "sale.order has carried the same field since 17.0",
    "native_evidence": "odoo/addons/sale/models/sale_order.py",
    "vendor_release": "n/a", "complexity_rationale": "",
    "upgrade_action_rationale": "", "notes": "",
    "oca_alt": "none",
    "oca_alt_evidence": "grepped acme, sale_acme across the OCA catalog",
}
record.update(json.loads(sys.argv[2]))
for key in filter(None, sys.argv[3].split(",")):
    del record[key]
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    json.dump(record, fh)
PY
}

wb_run() {  # <dir> -> sets wb_out (stdout+stderr) and wb_status
  wb_out="$(PYTHONPATH="$OPENPYXL_STUB" python3 "$WORKBOOK" --workdir "$1" \
    --target-version 19.0 -o "$1/out.xlsx" 2>&1)"
  wb_status=$?
}

# --- #1158: one record per module, nothing merged ----------------------------
dir="$(wb_case merge)"
wb_record "$dir"
wb_run "$dir"
expect "a complete build exits 0" "$wb_status" "0"
contains "a complete build reports no problems" "$wb_out" "problems: 0"
contains "a complete build reports no sentinels" "$wb_out" "sentinels: {}"

# --- #925: verdict and description are two columns ---------------------------
expect "16 columns, verdict and notes beside each other" \
  "$(csv_header "$dir/out_inventory.csv")" \
  "Module Name|Display Name|Module Purpose|Source version|LoC|Dependencies|Dependency origin|3rd party app?|3rd party app link|OCA repo|Complexity/risk|Upgrade action|Functional area|19 native?|19 native notes|OCA 19 alternative"
expect "verdict comes from the record" \
  "$(csv_cell "$dir/out_inventory.csv" acme '19 native?')" "yes/partial"
expect "notes come from the same record" \
  "$(csv_cell "$dir/out_inventory.csv" acme '19 native notes')" \
  "sale.order has carried the same field since 17.0"
expect "evidence comes from the same record" \
  "$(csv_cell "$dir/out_evidence.csv" acme 'native? evidence')" \
  "odoo/addons/sale/models/sale_order.py"

# --- #1146: the Upgrade action is proposed from native x OCA x 3rd party -----
# One case per row of the table in propose_action's docstring. An empty
# upgrade_action takes the proposal; `review` is a problem line.
action_case() {  # <name> <expected> <record-overrides-json> [problems-text]
  local d
  d="$(wb_case "act_$1")"
  wb_record "$d" "$3"
  wb_run "$d"
  expect "action $1: proposes $2" \
    "$(csv_cell "$d/out_inventory.csv" acme 'Upgrade action')" "$2"
  if [ -n "${4:-}" ]; then
    contains "action $1: problem line" "$wb_out" "$4"
  else
    contains "action $1: no problems" "$wb_out" "problems: 0"
  fi
}
RV='"upgrade_action": "", "upgrade_action_rationale": ""'
action_case yes merge-into-standard "{$RV, \"native\": \"yes\"}"
action_case yespartial merge-into-standard "{$RV, \"native\": \"yes/partial\"}"
action_case full replace "{$RV, \"native\": \"no\", \"oca_alt\": \"sale/sale_acme (full)\"}"
action_case already replace "{$RV, \"native\": \"no\", \"oca_alt\": \"already OCA: sale/acme\"}"
action_case partialalt review "{$RV, \"native\": \"no\", \"oca_alt\": \"sale/sale_acme (partial)\"}" \
  "acme: Upgrade action is review — decide keep/replace/merge-into-standard"
action_case inhouse keep "{$RV, \"native\": \"no\"}"
action_case vendor review "{$RV, \"native\": \"no\", \"third_party\": \"Yes\"}" \
  "acme: Upgrade action is review"
action_case partialnative review "{$RV, \"native\": \"partial\"}" \
  "acme: Upgrade action is review"
action_case sentinelnative review "{$RV, \"native\": \"TODO-AI\"}" \
  "acme: Upgrade action is review"

dir="$(wb_case override)"
wb_record "$dir" '{"native": "no", "upgrade_action": "replace"}'
wb_run "$dir"
contains "an override without a rationale is a problem" "$wb_out" \
  "acme: Upgrade action 'replace' overrides the proposed 'keep' without upgrade_action_rationale"
expect "an override without a rationale exits non-zero" \
  "$([ "$wb_status" -ne 0 ] && echo yes)" "yes"

dir="$(wb_case overrideok)"
wb_record "$dir" '{"native": "no", "upgrade_action": "replace", "upgrade_action_rationale": "vendor ships 19.0"}'
wb_run "$dir"
contains "an override with a rationale is accepted" "$wb_out" "problems: 0"
expect "the override is the delivered action" \
  "$(csv_cell "$dir/out_inventory.csv" acme 'Upgrade action')" "replace"

dir="$(wb_case seeddrop)"
printf '%s\n%s\n' "$SEED_HEADER" "${SEED_ROW/,keep,/,drop?,}" > "$dir/inventory.csv"
wb_record "$dir" "{$RV, \"native\": \"no\", \"oca_alt\": \"sale/sale_acme (full)\"}"
wb_run "$dir"
expect "the seed drop? wins over the proposal" \
  "$(csv_cell "$dir/out_inventory.csv" acme 'Upgrade action')" "drop?"
contains "the seed drop? is not a problem" "$wb_out" "problems: 0"

# --- #1158: oca_alt is read from the enrich record ---------------------------
dir="$(wb_case ocaalt)"
wb_record "$dir" '{"oca_alt": "sale/sale_acme (full)", "oca_alt_evidence": "found in OCA/sale"}'
wb_run "$dir"
expect "oca_alt comes from the enrich record" \
  "$(csv_cell "$dir/out_inventory.csv" acme 'OCA 19 alternative')" \
  "sale/sale_acme (full)"
expect "oca_alt_evidence comes from the enrich record" \
  "$(csv_cell "$dir/out_evidence.csv" acme 'OCA alternative evidence')" \
  "found in OCA/sale"
expect "an oca_alt_*.json file is not read" \
  "$(printf '[{"module":"acme","oca_alt":"x/y (full)"}]' > "$dir/oca_alt_g1.json"
     wb_run "$dir"; csv_cell "$dir/out_inventory.csv" acme 'OCA 19 alternative')" \
  "sale/sale_acme (full)"

# --- #1143: oca_index.json in the workdir wins over the record's oca_alt -----
dir="$(wb_case ocaindex)"
wb_record "$dir" '{"oca_alt": "sale/sale_acme (full)", "oca_alt_evidence": "found in OCA/sale"}'
printf '{"series": ["19.0"], "modules": {"acme": ["sale"]}, "oca_alt": {"acme": "already OCA: sale/acme"}, "mismatches": []}' > "$dir/oca_index.json"
wb_run "$dir"
expect "the index literal overrides the record oca_alt" \
  "$(csv_cell "$dir/out_inventory.csv" acme 'OCA 19 alternative')" \
  "already OCA: sale/acme"
expect "the evidence names the index" \
  "$(csv_cell "$dir/out_evidence.csv" acme 'OCA alternative evidence')" \
  "oca_check.py 19.0 index"

# --- #1158: a missing file, an array file, a missing or extra key ------------
dir="$(wb_case nofile)"
wb_run "$dir"
contains "a missing record is a problem line" "$wb_out" \
  "acme: enrich_acme.json is missing"
expect "a missing record exits non-zero" "$wb_status" "1"

dir="$(wb_case array)"
printf '[{"module": "acme", "native": "no"}]' > "$dir/enrich_acme.json"
wb_run "$dir"
contains "an array file is a problem line" "$wb_out" \
  "acme: enrich_acme.json is not one JSON object"
expect "an array file exits non-zero" "$wb_status" "1"

dir="$(wb_case twofiles)"
wb_record "$dir" '{"module": "other"}'
wb_run "$dir"
contains "a record naming another module is a problem line" "$wb_out" \
  "acme: enrich_acme.json claims module 'other'"
expect "a record naming another module exits non-zero" "$wb_status" "1"

dir="$(wb_case missingkey)"
wb_record "$dir" '{}' "vendor_release"
wb_run "$dir"
contains "a missing key is a problem line" "$wb_out" \
  "acme: enrich_acme.json is missing key(s) vendor_release"
expect "a missing key exits non-zero" "$wb_status" "1"

dir="$(wb_case extrakey)"
wb_record "$dir" '{"coverage_gap": "none"}'
wb_run "$dir"
contains "an extra key is a problem line" "$wb_out" \
  "acme: enrich_acme.json has extra key(s) coverage_gap"
expect "an extra key exits non-zero" "$wb_status" "1"

dir="$(wb_case sentinelkey)"
wb_record "$dir" '{"vendor_release": "TODO-AI"}'
wb_run "$dir"
contains "a sentinel in an evidence key is a problem line" "$wb_out" \
  "acme: enrich_acme.json key vendor_release is TODO-AI"
expect "a sentinel in an evidence key exits non-zero" "$wb_status" "1"

# --- #1144: the legacy single-string cell is a problem, not split ------------
dir="$(wb_case legacy)"
wb_record "$dir" '{"native": "yes/partial: mail composer has cc/bcc fields", "native_notes": "", "native_evidence": "addons/mail/wizard/mail_compose_message.py"}'
wb_run "$dir"
contains "a legacy '<verdict>: <text>' cell is a problem line" "$wb_out" \
  "acme: 19 native? is 'yes/partial: mail composer has cc/bcc fields' — expected exactly one of yes, yes/partial, partial, no"
expect "legacy cell is not split into a notes cell" \
  "$(csv_cell "$dir/out_inventory.csv" acme '19 native notes')" ""
expect "legacy cell exits non-zero" "$wb_status" "1"

# --- #925: anything outside the four verdicts is a problem -------------------
dir="$(wb_case verdict)"
wb_record "$dir" '{"native": "maybe", "native_notes": "half of it"}'
wb_run "$dir"
contains "a verdict outside the vocabulary is rejected" "$wb_out" \
  "acme: 19 native? is 'maybe' — expected exactly one of yes, yes/partial, partial, no"
expect "a bad verdict exits non-zero" "$wb_status" "1"

# --- #919: the documented 300-char evidence cap is enforced ------------------
dir="$(wb_case evidence)"
wb_record "$dir" "{\"native\": \"no\", \"native_notes\": \"nothing in 19 covers it\", \"native_evidence\": \"$(printf 'x%.0s' $(seq 301))\"}"
wb_run "$dir"
contains "301 chars of native evidence is one char too many" "$wb_out" \
  "acme: native? evidence is 301 chars (max 300)"
expect "over-long evidence exits non-zero" "$wb_status" "1"

# --- #909: a sentinel in a delivered column is unanswered, not valid ---------
dir="$(wb_case sentinel)"
wb_record "$dir" '{"native": "TODO-AI"}'
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

for f in "$SUT" "$WORKBOOK" "$OCA_CHECK" "$INVENTORY_MD" "$METHOD_MD"; do
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
expect "no 'self:' verdict literal in the inventory reference" \
  "$(grep -c 'self:' "$INVENTORY_MD")" "0"

# ...and the action table (the docstring of propose_action) puts the value in
# the column that takes it: `already OCA:` is an `OCA <major> alternative`
# value, not a `native?` one.
contains "the action table names the literal in the OCA column" \
  "$(cat "$WORKBOOK")" '| no            | `already OCA: ...`'
expect "the deleted oca-base-review.md has no link left" \
  "$(grep -rl --include='*.md' --include='*.py' 'oca-base-review' \
    "$SKILLS_DIR/odoo-upgrade" | wc -l | tr -d ' ')" "0"
contains "the prior-art decision table names it in the oca column" \
  "$(cat "$METHOD_MD")" '| `no` | `already OCA: <repo>/<module>` |'
contains "the prior-art verdict list names the literal" \
  "$(cat "$METHOD_MD")" '`already OCA: <repo>/<module>`'

echo "{\"passed\": $pass, \"failed\": $fail}"
[ "$fail" -eq 0 ]
