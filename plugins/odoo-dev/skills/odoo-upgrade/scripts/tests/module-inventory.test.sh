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
# so a silent change here is a silent miss there.
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
# inventory, Module in the Evidence and Traceability siblings — so one reader
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

# --- #925: the legacy single-string cell is split, not rejected --------------
dir="$(wb_case legacy)"
cat > "$dir/enrich_g1.json" <<'JSON'
[{"module": "acme", "native": "yes/partial: mail composer has cc/bcc fields",
  "native_evidence": "addons/mail/wizard/mail_compose_message.py"}]
JSON
wb_run "$dir"
expect "legacy cell yields the verdict" \
  "$(csv_cell "$dir/out_inventory.csv" acme '19 native?')" "yes/partial"
expect "legacy cell yields the description" \
  "$(csv_cell "$dir/out_inventory.csv" acme '19 native notes')" \
  "mail composer has cc/bcc fields"
contains "legacy cell is flagged for review" "$wb_out" "carried the legacy"
contains "legacy cell does not block the build" "$wb_out" "problems: 0"
expect "legacy cell exits 0" "$wb_status" "0"

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

# --- #911: a headers-only register is staged, not absent ---------------------
dir="$(wb_case tickets)"
cat > "$dir/enrich_g1.json" <<'JSON'
[{"module": "acme", "native": "no", "native_notes": "nothing in 19 covers it"}]
JSON
printf '%s\n' \
  "id,date,subject,token,status,blocking_module,resolution,link" \
  > "$dir/tickets.csv"
wb_run "$dir"
contains "a staged register counts itself" "$wb_out" \
  "tickets: 0 total, 0 still blocking"
contains "a staged register gets its sheet" "$wb_out" "('Tickets', 0)"
expect "a staged register gets its CSV sibling, headers intact" \
  "$(csv_header "$dir/out_tickets.csv")" \
  "id|date|subject|token|status|blocking_module|resolution|link"
expect "a staged register is not a problem" "$wb_status" "0"

# =============================================================================
# build_workbook.py — the functional-requirements validator
# =============================================================================
# Phase 3 is a fan-out of one agent per group, each writing one fr_<GROUP>.json,
# and this report is the only thing that reads all of them: it is what proves
# the agents followed their briefs. So each case plants exactly one defect and
# reads the line it must produce — and where the point is that a rule must NOT
# fire, it reads the exact problem count instead, since a count is the only
# assertion a stray extra line can fail.

# The inventory half kept complete and valid, so every problem a requirements
# case sees is one it wrote into its own fr_*.json.
fr_case() {  # <name> -> echoes the dir
  local dir
  dir="$(wb_case "$1")"
  cat > "$dir/enrich_g1.json" <<'JSON'
[{"module": "acme", "native": "no", "native_notes": "nothing in 19 covers it",
  "native_evidence": "grepped sale.order across the 19.0 tree",
  "vendor_release": "n/a"}]
JSON
  printf '%s' "$dir"
}

# --- #910: a missing key is a problem line, not a KeyError -------------------
# The entry is short two keys; the run must still reach the report, the
# counters and the CSV sibling, because one malformed entry anywhere would
# otherwise suppress the feedback for every other agent in the fan-out.
dir="$(fr_case fr_missing_key)"
cat > "$dir/fr_A.json" <<'JSON'
[{"tmp_id": "A-01",
  "requirement": "When a sales user confirms a sales order, the system shall record the confirmation date.",
  "type": "Business rule", "functional_area": "Sales", "actor": "Sales user",
  "status_source": "active", "status_note": "", "handled": "no",
  "handled_by": "custom code", "handled_notes": "",
  "verification": "Confirm a sales order and read the date on it",
  "notes": ""}]
JSON
wb_run "$dir"
contains "a missing key names every key that differs" "$wb_out" \
  "A-01: key mismatch ['evidence', 'sources']"
contains "a missing key does not stop the report" "$wb_out" "handled: {'no': 1}"
expect "a missing key exits non-zero" "$wb_status" "1"
expect "the entry still reaches the requirements CSV" \
  "$(csv_cell "$dir/out_requirements.csv" FR-001 'Requirement')" \
  "When a sales user confirms a sales order, the system shall record the confirmation date."
expect "the missing key is an empty cell, not a crash" \
  "$(csv_cell "$dir/out_requirements.csv" FR-001 'Evidence')" ""

# --- #912: a bare string in sources is one line, not one per letter ----------
dir="$(fr_case fr_sources_string)"
cat > "$dir/fr_A.json" <<'JSON'
[{"tmp_id": "A-01",
  "requirement": "When a sales user confirms a sales order, the system shall record the confirmation date.",
  "type": "Business rule", "functional_area": "Sales", "actor": "Sales user",
  "sources": "acme", "evidence": "models/sale_order.py",
  "status_source": "active", "status_note": "", "handled": "no",
  "handled_by": "custom code", "handled_notes": "",
  "verification": "Confirm a sales order and read the date on it",
  "notes": ""}]
JSON
wb_run "$dir"
contains "a mistyped sources names the type it got" "$wb_out" \
  "A-01: sources must be a list of module names, got str"
# 2 = the type line + the module nothing now covers. Four letters of "acme"
# would have been four more.
contains "the string is not iterated letter by letter" "$wb_out" "problems: 2"
expect "a mistyped sources exits non-zero" "$wb_status" "1"

# --- #917: the shall check is case-insensitive, and says so ------------------
dir="$(fr_case fr_shall_case)"
cat > "$dir/fr_A.json" <<'JSON'
[{"tmp_id": "A-01",
  "requirement": "When a sales user confirms a sales order, the system Shall record the confirmation date.",
  "type": "Business rule", "functional_area": "Sales", "actor": "Sales user",
  "sources": ["acme"], "evidence": "models/sale_order.py",
  "status_source": "active", "status_note": "", "handled": "no",
  "handled_by": "custom code", "handled_notes": "",
  "verification": "Confirm a sales order and read the date on it",
  "notes": ""},
 {"tmp_id": "A-02",
  "requirement": "The system records the confirmation date on a sales order.",
  "type": "Business rule", "functional_area": "Sales", "actor": "Sales user",
  "sources": ["acme"], "evidence": "models/sale_order.py",
  "status_source": "active", "status_note": "", "handled": "no",
  "handled_by": "custom code", "handled_notes": "",
  "verification": "Confirm a sales order and read the date on it",
  "notes": ""}]
JSON
wb_run "$dir"
contains "the no-shall line states the case rule" "$wb_out" \
  "A-02: no \"shall\" (case-insensitive) in:"
# 1 = A-02 alone: a capitalised Shall is the keyword, not a missing one.
contains "a capitalised Shall passes" "$wb_out" "problems: 1"
expect "a requirement with no shall exits non-zero" "$wb_status" "1"

# --- #918: weak wording is a labelled rule, not a substring grep -------------
dir="$(fr_case fr_weak_wording)"
cat > "$dir/fr_A.json" <<'JSON'
[{"tmp_id": "A-01",
  "requirement": "The system shall be able to support all applicable discounts where appropriate.",
  "type": "Business rule", "functional_area": "Sales", "actor": "Sales user",
  "sources": ["acme"], "evidence": "models/sale_order.py",
  "status_source": "active", "status_note": "", "handled": "no",
  "handled_by": "custom code", "handled_notes": "",
  "verification": "Apply a discount and read the total",
  "notes": ""},
 {"tmp_id": "A-02",
  "requirement": "When a sales user selects the `normal` delivery type, the system shall reserve the stock.",
  "type": "Business rule", "functional_area": "Sales", "actor": "Sales user",
  "sources": ["acme"], "evidence": "models/sale_order.py",
  "status_source": "active", "status_note": "", "handled": "no",
  "handled_by": "custom code", "handled_notes": "",
  "verification": "Select the delivery type and read the reservation",
  "notes": ""},
 {"tmp_id": "A-03",
  "requirement": "The system shall render the vendor report faster than the 16.0 report and may notify the buyer.",
  "type": "Reporting/Notification", "functional_area": "Sales",
  "actor": "Sales user",
  "sources": ["acme"], "evidence": "report/sale_report.xml",
  "status_source": "active", "status_note": "", "handled": "no",
  "handled_by": "custom code", "handled_notes": "",
  "verification": "Print the report and time it",
  "notes": ""}]
JSON
wb_run "$dir"
contains "a capability is not a behaviour" "$wb_out" \
  "A-01: weak wording [superfluous infinitive] 'shall be able to':"
contains "an absolute names no set" "$wb_out" "A-01: weak wording [absolute] 'all':"
contains "the original adjectives still fire" "$wb_out" \
  "A-01: weak wording [vague adjective] 'appropriate':"
contains "a modal is caught by the verb after it" "$wb_out" \
  "A-03: weak wording [modal verb] 'may':"
# 4 = three on A-01 and one on A-03. A-02's weak word is inside backticks, so
# it is the client's own term for a delivery type, and A-03's comparative
# names its baseline with `than`: neither is a defect and neither adds a line.
contains "a quoted literal and a referenced comparative are not weak" \
  "$wb_out" "problems: 4"
expect "weak wording exits non-zero" "$wb_status" "1"

# --- #916: groups are compared whole, not by first character -----------------
# A1 and A2 share a first character, which is what used to make every pair look
# intra-group and silently switch duplicate review off.
dir="$(fr_case fr_groups_differ)"
cat > "$dir/fr_A1.json" <<'JSON'
[{"tmp_id": "A1-01",
  "requirement": "When a sales user confirms a sales order, the system shall record the confirmation date on the delivery.",
  "type": "Business rule", "functional_area": "Sales", "actor": "Sales user",
  "sources": ["acme"], "evidence": "models/sale_order.py",
  "status_source": "active", "status_note": "", "handled": "no",
  "handled_by": "custom code", "handled_notes": "",
  "verification": "Confirm a sales order and read the delivery",
  "notes": ""}]
JSON
cat > "$dir/fr_A2.json" <<'JSON'
[{"tmp_id": "A2-01",
  "requirement": "When a sales user confirms a sales order, the system shall record the confirmation date on the invoice.",
  "type": "Business rule", "functional_area": "Sales", "actor": "Sales user",
  "sources": ["acme"], "evidence": "models/sale_order.py",
  "status_source": "active", "status_note": "", "handled": "no",
  "handled_by": "custom code", "handled_notes": "",
  "verification": "Confirm a sales order and read the invoice",
  "notes": ""}]
JSON
wb_run "$dir"
contains "two groups sharing a first character are still two groups" "$wb_out" \
  "near-duplicate"
contains "the pair is listed once, for review" "$wb_out" \
  "to review (not blocking): 1"
expect "a near-duplicate is not blocking" "$wb_status" "0"

# --- #916: one agent's own file is still its own business --------------------
dir="$(fr_case fr_groups_same)"
cat > "$dir/fr_A1.json" <<'JSON'
[{"tmp_id": "A1-01",
  "requirement": "When a sales user confirms a sales order, the system shall record the confirmation date on the delivery.",
  "type": "Business rule", "functional_area": "Sales", "actor": "Sales user",
  "sources": ["acme"], "evidence": "models/sale_order.py",
  "status_source": "active", "status_note": "", "handled": "no",
  "handled_by": "custom code", "handled_notes": "",
  "verification": "Confirm a sales order and read the delivery",
  "notes": ""},
 {"tmp_id": "A1-02",
  "requirement": "When a sales user confirms a sales order, the system shall record the confirmation date on the invoice.",
  "type": "Business rule", "functional_area": "Sales", "actor": "Sales user",
  "sources": ["acme"], "evidence": "models/sale_order.py",
  "status_source": "active", "status_note": "", "handled": "no",
  "handled_by": "custom code", "handled_notes": "",
  "verification": "Confirm a sales order and read the invoice",
  "notes": ""}]
JSON
wb_run "$dir"
contains "a pair inside one group is not reviewed" "$wb_out" \
  "to review (not blocking): 0"
expect "an intra-group pair is clean" "$wb_status" "0"

echo "{\"passed\": $pass, \"failed\": $fail}"
[ "$fail" -eq 0 ]
