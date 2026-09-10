#!/usr/bin/env bash
# studio-inventory.test.sh — build a miniature Odoo-shaped database, plant one
# artifact of each kind, and assert what studio_inventory.py finds and how it
# classifies it.
#
# Against a real customer database the script reports whatever is there, which
# proves the SQL parses but not that the classification is right. This does the
# opposite: a known population, checked row by row.
#
# Needs a postgres it can CREATE DATABASE on (libpq env). The database is
# dropped on every exit path.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUT="$(cd "$SCRIPT_DIR/.." && pwd)/studio_inventory.py"
DB="${STUDIO_TEST_DB:-studio_inventory_test_$$}"

command -v psql >/dev/null 2>&1 || { echo "SKIP: psql not available"; exit 0; }
pg_isready >/dev/null 2>&1 || { echo "SKIP: no reachable postgres"; exit 0; }

cleanup() { dropdb --if-exists "$DB" >/dev/null 2>&1 || true; }
trap cleanup EXIT
dropdb --if-exists "$DB" >/dev/null 2>&1 || true
createdb "$DB" || { echo "SKIP: cannot create a database here"; exit 0; }

psql -q -d "$DB" >/dev/null <<'SQL'
CREATE TABLE ir_module_module (id serial PRIMARY KEY, name text, latest_version text);
INSERT INTO ir_module_module (name, latest_version) VALUES ('base', '17.0.1.3');

CREATE TABLE ir_model (id serial PRIMARY KEY, model text, name text, state text);
CREATE TABLE ir_model_fields (
  id serial PRIMARY KEY, name text, model text, field_description text,
  ttype text, relation text, compute text, store boolean, state text);
CREATE TABLE ir_model_data (
  id serial PRIMARY KEY, module text, name text, model text, res_id integer);
CREATE TABLE ir_ui_view (
  id serial PRIMARY KEY, name text, model text, type text,
  active boolean DEFAULT true, inherit_id integer);
CREATE TABLE ir_act_server (
  id serial PRIMARY KEY, name text, model_name text, state text,
  model_id integer, ir_actions_server_id integer);
CREATE TABLE ir_cron (
  id serial PRIMARY KEY, active boolean DEFAULT true, ir_actions_server_id integer);
CREATE TABLE ir_act_report_xml (
  id serial PRIMARY KEY, name text, model text, report_name text);

-- a Studio field (customer data -> must become real code)
INSERT INTO ir_model_fields (id, name, model, field_description, ttype, state)
  VALUES (1, 'x_studio_carrier_account', 'res.partner', 'Carrier Account', 'char', 'manual');
-- a hand-made manual field, same treatment
INSERT INTO ir_model_fields (id, name, model, field_description, ttype, state)
  VALUES (2, 'x_legacy_code', 'sale.order', 'Legacy Code', 'char', 'manual');
-- a normal field, which must NOT appear
INSERT INTO ir_model_fields (id, name, model, field_description, ttype, state)
  VALUES (3, 'name', 'res.partner', 'Name', 'char', 'base');

INSERT INTO ir_model (id, model, name, state) VALUES (1, 'x_inspection', 'Inspection', 'manual');
INSERT INTO ir_model (id, model, name, state) VALUES (2, 'res.partner', 'Contact', 'base');

-- a Studio-owned view, an abandoned inactive view, and a normal module view
INSERT INTO ir_ui_view (id, name, model, type, active) VALUES (1, 'studio form', 'res.partner', 'form', true);
INSERT INTO ir_model_data (module, name, model, res_id) VALUES ('studio_customization', 'v1', 'ir.ui.view', 1);
INSERT INTO ir_ui_view (id, name, model, type, active) VALUES (2, 'broken form', 'sale.order', 'form', false);
INSERT INTO ir_ui_view (id, name, model, type, active) VALUES (3, 'module form', 'sale.order', 'form', true);
INSERT INTO ir_model_data (module, name, model, res_id) VALUES ('sale', 'v3', 'ir.ui.view', 3);

-- an unowned code server action, and one a module owns
INSERT INTO ir_act_server (id, name, model_name, state) VALUES (1, 'UI action', 'sale.order', 'code');
INSERT INTO ir_act_server (id, name, model_name, state) VALUES (2, 'Module action', 'sale.order', 'code');
INSERT INTO ir_model_data (module, name, model, res_id) VALUES ('sale', 'a2', 'ir.actions.server', 2);

-- an unowned active cron, and an unowned inactive one
INSERT INTO ir_act_server (id, name, model_name, state) VALUES (3, 'Nightly sync', 'sale.order', 'code');
INSERT INTO ir_cron (id, active, ir_actions_server_id) VALUES (1, true, 3);
INSERT INTO ir_act_server (id, name, model_name, state) VALUES (4, 'Dead sync', 'sale.order', 'code');
INSERT INTO ir_cron (id, active, ir_actions_server_id) VALUES (2, false, 4);

INSERT INTO ir_act_report_xml (id, name, model, report_name)
  VALUES (1, 'Custom Picking', 'stock.picking', 'x_custom_picking');
SQL

out="$(python3 "$SUT" --db "$DB" --csv "$SCRIPT_DIR/.studio.csv" 2>&1 | tail -1)"
rows="$(python3 -c "
import csv, sys
with open(sys.argv[1]) as fh:
    print(list(csv.DictReader(fh)))" "$SCRIPT_DIR/.studio.csv" 2>/dev/null || echo '[]')"

pass=0; fail=0
expect() { if [ "$2" = "$3" ]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL $1: wanted '$3', got '$2'" >&2; fi; }
count() { python3 -c "
import json,sys
print(json.loads(sys.argv[1])['counts'].get(sys.argv[2], 0))" "$out" "$1"; }
cls() { python3 -c "
import csv,sys
for r in csv.DictReader(open(sys.argv[1])):
    if r['technical_name'] == sys.argv[2] or r['label'] == sys.argv[2]:
        print(r['classification']); break
else: print('MISSING')" "$SCRIPT_DIR/.studio.csv" "$1"; }

expect "manual fields only"        "$(count field)" "2"
expect "manual models only"        "$(count model)" "1"
# The Studio view and the inactive one; the module-owned active view is not a
# finding.
expect "studio + inactive views"   "$(count view)" "2"
expect "unowned actions + crons"   "$(count server_action)" "1"
expect "crons counted separately"  "$(count cron)" "2"
expect "unowned reports"           "$(count report)" "1"

expect "studio field -> code"      "$(cls x_studio_carrier_account)" "convert-to-code"
expect "manual field -> code"      "$(cls x_legacy_code)" "convert-to-code"
expect "manual model -> code"      "$(cls x_inspection)" "convert-to-code"
expect "studio view -> code"       "$(cls 'studio form')" "convert-to-code"
# An inactive view is either abandoned work or an earlier upgrade casualty.
# Neither is safe to classify without a human.
expect "inactive view -> review"   "$(cls 'broken form')" "review"
expect "unowned code action"       "$(cls 'UI action')" "convert-to-code"
expect "active unowned cron"       "$(cls 'Nightly sync')" "convert-to-code"
expect "inactive cron -> purge"    "$(cls 'Dead sync')" "purge"
expect "unowned report -> code"    "$(cls 'Custom Picking')" "convert-to-code"
expect "module view is not a finding" "$(cls 'module form')" "MISSING"
expect "module action is not a finding" "$(cls 'Module action')" "MISSING"
expect "normal field is not a finding"  "$(cls name)" "MISSING"

expect "version read from base module" "$(python3 -c "
import json,sys; print(json.loads(sys.argv[1])['odoo_version'])" "$out")" "17.0.1.3"
expect "rows are not dumped on stdout" "$(python3 -c "
import json,sys; print('rows' in json.loads(sys.argv[1]))" "$out")" "False"

rm -f "$SCRIPT_DIR/.studio.csv"
echo "{\"passed\": $pass, \"failed\": $fail}"
[ "$fail" -eq 0 ]
