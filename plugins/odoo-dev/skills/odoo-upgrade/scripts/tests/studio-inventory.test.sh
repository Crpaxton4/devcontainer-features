#!/usr/bin/env bash
# studio-inventory.test.sh — build a miniature Odoo-shaped database, plant one
# artifact of each kind, and assert what studio_inventory.py finds and how it
# classifies it.
#
# Against a real customer database the script reports whatever is there, which
# proves the SQL parses but not that the classification is right. This does the
# opposite: a known population, checked row by row.
#
# The fixture is 17.0-shaped where the schema differs across the supported
# series: translated labels are jsonb, base_automation has no action_server_id
# and ir_act_server points back at it, and ir_ui_view carries arch_fs /
# arch_updated. Those are exactly the shapes the script used to get wrong.
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
CREATE TABLE ir_module_module (
  id serial PRIMARY KEY, name text, latest_version text, write_date timestamp);
INSERT INTO ir_module_module (name, latest_version, write_date) VALUES
  ('base', '17.0.1.3', '2024-01-01'), ('sale', '17.0.1.0', '2024-01-01'),
  ('stock', '17.0.1.0', '2024-01-01'), ('crm', '17.0.1.0', '2024-01-01');

-- The database runs in French: en_US is absent from some labels, so the
-- fallback to the active language is what makes them readable.
CREATE TABLE res_lang (id serial PRIMARY KEY, code text, active boolean);
INSERT INTO res_lang (code, active) VALUES ('fr_FR', true);

-- Translated columns are jsonb from 16.0 on.
CREATE TABLE ir_model (id serial PRIMARY KEY, model text, name jsonb, state text);
CREATE TABLE ir_model_fields (
  id serial PRIMARY KEY, name text, model text, field_description jsonb,
  ttype text, relation text, compute text, store boolean, state text);
CREATE TABLE ir_model_data (
  id serial PRIMARY KEY, module text, name text, model text, res_id integer,
  noupdate boolean DEFAULT false);
-- ir.ui.view.name is NOT translated, so it stays plain text: the script must
-- keep reading a varchar label as well as a jsonb one.
CREATE TABLE ir_ui_view (
  id serial PRIMARY KEY, name text, model text, type text,
  active boolean DEFAULT true, inherit_id integer,
  arch_fs text, arch_db text, arch_updated boolean DEFAULT false,
  write_uid integer, write_date timestamp);
CREATE TABLE ir_act_server (
  id serial PRIMARY KEY, name jsonb, model_name text, state text,
  model_id integer, ir_actions_server_id integer, base_automation_id integer);
-- 17.0 shape: no action_server_id here, the link is ir_act_server.base_automation_id.
CREATE TABLE base_automation (
  id serial PRIMARY KEY, active boolean DEFAULT true, trigger text);
CREATE TABLE ir_cron (
  id serial PRIMARY KEY, active boolean DEFAULT true, ir_actions_server_id integer);
CREATE TABLE ir_act_report_xml (
  id serial PRIMARY KEY, name jsonb, model text, report_name text);

-- The model tables the populated check reads.
CREATE TABLE res_partner (
  id serial PRIMARY KEY, x_studio_carrier_account text, x_studio_flag boolean);
INSERT INTO res_partner (x_studio_carrier_account, x_studio_flag)
  VALUES ('ACME-1', false), (NULL, false);
CREATE TABLE sale_order (id serial PRIMARY KEY, x_legacy_code text);
INSERT INTO sale_order (x_legacy_code) VALUES (''), (NULL);

-- a Studio field (customer data -> must become real code), and it holds data
INSERT INTO ir_model_fields (id, name, model, field_description, ttype, store, state)
  VALUES (1, 'x_studio_carrier_account', 'res.partner',
          '{"en_US": "Carrier Account"}', 'char', true, 'manual');
-- a hand-made manual field, same treatment; its label has no en_US key, and
-- every row is the empty string
INSERT INTO ir_model_fields (id, name, model, field_description, ttype, store, state)
  VALUES (2, 'x_legacy_code', 'sale.order', '{"fr_FR": "Code Legacy"}',
          'char', true, 'manual');
-- a normal field, which must NOT appear
INSERT INTO ir_model_fields (id, name, model, field_description, ttype, store, state)
  VALUES (3, 'name', 'res.partner', '{"en_US": "Name"}', 'char', true, 'base');
-- a non-stored computed field: no column, so nothing to measure
INSERT INTO ir_model_fields (id, name, model, field_description, ttype, compute, store, state)
  VALUES (4, 'x_studio_margin', 'res.partner', '{"en_US": "Margin"}', 'float',
          'for r in self: r.x_studio_margin = 0', false, 'manual');
-- a boolean nobody ever ticked: NOT NULL on every row, and still empty
INSERT INTO ir_model_fields (id, name, model, field_description, ttype, store, state)
  VALUES (5, 'x_studio_flag', 'res.partner', '{"en_US": "Flag"}', 'boolean',
          true, 'manual');
-- a field on a model with no table at all: unmeasurable, which is not empty
INSERT INTO ir_model_fields (id, name, model, field_description, ttype, store, state)
  VALUES (6, 'x_studio_orphan', 'x.gone', '{"en_US": "Orphan"}', 'char',
          true, 'manual');

INSERT INTO ir_model (id, model, name, state)
  VALUES (1, 'x_inspection', '{"en_US": "Inspection"}', 'manual');
INSERT INTO ir_model (id, model, name, state)
  VALUES (2, 'res.partner', '{"en_US": "Contact"}', 'base');

-- a Studio-owned view, an abandoned inactive view, and a normal module view
INSERT INTO ir_ui_view (id, name, model, type, active, write_uid, write_date)
  VALUES (1, 'studio form', 'res.partner', 'form', true, 1, '2023-01-01');
INSERT INTO ir_model_data (module, name, model, res_id) VALUES ('studio_customization', 'v1', 'ir.ui.view', 1);
INSERT INTO ir_ui_view (id, name, model, type, active, write_uid, write_date)
  VALUES (2, 'broken form', 'sale.order', 'form', false, 1, '2023-01-01');
INSERT INTO ir_ui_view (id, name, model, type, active, write_uid, write_date)
  VALUES (3, 'module form', 'sale.order', 'form', true, 1, '2023-01-01');
INSERT INTO ir_model_data (module, name, model, res_id) VALUES ('sale', 'v3', 'ir.ui.view', 3);

-- signal 1: shipped from a file, arch since edited in the database
INSERT INTO ir_ui_view (id, name, model, type, active, arch_fs, arch_updated, write_uid, write_date)
  VALUES (4, 'edited list', 'stock.move.line', 'list', true,
          'stock/views/stock_move_line_views.xml', true, 1, '2023-01-01');
INSERT INTO ir_model_data (module, name, model, res_id) VALUES ('stock', 'v4', 'ir.ui.view', 4);
-- signal 2: the xmlid was flipped to noupdate, which is how a UI edit survives
INSERT INTO ir_ui_view (id, name, model, type, active, arch_fs, write_uid, write_date)
  VALUES (5, 'noupdate form', 'res.users', 'form', true,
          'base/views/res_users_views.xml', 1, '2023-01-01');
INSERT INTO ir_model_data (module, name, model, res_id, noupdate)
  VALUES ('base', 'v5', 'ir.ui.view', 5, true);
-- signal 3: a real user wrote it after the owning module was last updated
INSERT INTO ir_ui_view (id, name, model, type, active, arch_fs, write_uid, write_date)
  VALUES (6, 'late edit form', 'crm.lead', 'form', true,
          'crm/views/crm_lead_views.xml', 7, '2024-06-01');
INSERT INTO ir_model_data (module, name, model, res_id) VALUES ('crm', 'v6', 'ir.ui.view', 6);
-- edited, and no xmlid at all: still a finding, with no module to blame
INSERT INTO ir_ui_view (id, name, model, type, active, arch_fs, arch_updated, write_uid, write_date)
  VALUES (7, 'website page', 'ir.ui.view', 'qweb', true,
          'website/views/website_templates.xml', true, 1, '2023-01-01');

-- an unowned code server action, and one a module owns
INSERT INTO ir_act_server (id, name, model_name, state) VALUES (1, '{"en_US": "UI action"}', 'sale.order', 'code');
INSERT INTO ir_act_server (id, name, model_name, state) VALUES (2, '{"en_US": "Module action"}', 'sale.order', 'code');
INSERT INTO ir_model_data (module, name, model, res_id) VALUES ('sale', 'a2', 'ir.actions.server', 2);

-- an unowned active cron, and an unowned inactive one
INSERT INTO ir_act_server (id, name, model_name, state) VALUES (3, '{"en_US": "Nightly sync"}', 'sale.order', 'code');
INSERT INTO ir_cron (id, active, ir_actions_server_id) VALUES (1, true, 3);
INSERT INTO ir_act_server (id, name, model_name, state) VALUES (4, '{"en_US": "Dead sync"}', 'sale.order', 'code');
INSERT INTO ir_cron (id, active, ir_actions_server_id) VALUES (2, false, 4);

-- a base automation, 17.0 shape: the server action points back at it, and
-- carries the model name. Given an xmlid so it is not also reported as an
-- unowned server action.
INSERT INTO ir_act_server (id, name, model_name, state, base_automation_id)
  VALUES (5, '{"en_US": "Notify on confirm"}', 'sale.order', 'code', 1);
INSERT INTO base_automation (id, active, trigger) VALUES (1, true, 'on_create');
INSERT INTO ir_model_data (module, name, model, res_id) VALUES ('sale', 'a5', 'ir.actions.server', 5);

INSERT INTO ir_act_report_xml (id, name, model, report_name)
  VALUES (1, '{"en_US": "Custom Picking"}', 'stock.picking', 'x_custom_picking');
SQL

out="$(python3 "$SUT" --db "$DB" --csv "$SCRIPT_DIR/.studio.csv" 2>&1 | tail -1)"

pass=0; fail=0
expect() { if [ "$2" = "$3" ]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL $1: wanted '$3', got '$2'" >&2; fi; }
count() { python3 -c "
import json,sys
print(json.loads(sys.argv[1])['counts'].get(sys.argv[2], 0))" "$out" "$1"; }
# cell <row> <column> — <row> matches on technical_name or label.
cell() { python3 -c "
import csv,sys
for r in csv.DictReader(open(sys.argv[1])):
    if r['technical_name'] == sys.argv[2] or r['label'] == sys.argv[2]:
        print(r[sys.argv[3]]); break
else: print('MISSING')" "$SCRIPT_DIR/.studio.csv" "$1" "$2"; }
cls() { cell "$1" classification; }

expect "manual fields only"        "$(count field)" "5"
expect "manual models only"        "$(count model)" "1"
# The Studio view and the inactive one; the module-owned unedited view is not a
# finding.
expect "studio + inactive views"   "$(count view)" "2"
# The four module-owned views the database no longer agrees with.
expect "hand-edited module views"  "$(count view-inline-edit)" "4"
expect "unowned actions + crons"   "$(count server_action)" "1"
expect "crons counted separately"  "$(count cron)" "2"
expect "base automations"          "$(count automation)" "1"
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

# --- #928: translated jsonb labels arrive as text, not as a JSON object -------
expect "field label is plain text" "$(cell x_studio_carrier_account label)" "Carrier Account"
# No en_US key: the database's own active language answers instead.
expect "label falls back to active language" "$(cell x_legacy_code label)" "Code Legacy"
expect "model label is plain text" "$(cell x_inspection label)" "Inspection"
expect "action label is plain text" "$(cell 'ir.actions.server:1' label)" "UI action"
expect "report label is plain text" "$(cell x_custom_picking label)" "Custom Picking"
expect "view label (plain varchar) still reads" "$(cell 'view:1' label)" "studio form"
expect "no jsonb leaked into any cell" "$(python3 -c "
import csv,sys
bad = [(r['kind'], v) for r in csv.DictReader(open(sys.argv[1]))
       for v in r.values() if v and v.lstrip().startswith('{')]
print(bad or 'clean')" "$SCRIPT_DIR/.studio.csv")" "clean"

# --- #929: does the field hold anything ---------------------------------------
expect "stored field with data"    "$(cell x_studio_carrier_account populated)" "populated"
expect "stored field of empty strings" "$(cell x_legacy_code populated)" "empty"
# A boolean nobody ticked is NOT NULL everywhere; a plain null test would call
# it populated.
expect "unticked boolean is empty" "$(cell x_studio_flag populated)" "empty"
expect "non-stored field is n/a"   "$(cell x_studio_margin populated)" "n/a"
expect "field with no table is n/a" "$(cell x_studio_orphan populated)" "n/a"
expect "non-field rows leave it blank" "$(cell x_inspection populated)" ""

# --- #930: module views whose arch was edited in the database ------------------
expect "arch_updated view -> review" "$(cls 'edited list')" "review"
expect "noupdate view -> review"     "$(cls 'noupdate form')" "review"
expect "late human edit -> review"   "$(cls 'late edit form')" "review"
expect "owning module named"         "$(cell 'edited list' owner_module)" "stock"
expect "noupdate owner named"        "$(cell 'noupdate form' owner_module)" "base"
expect "no xmlid is said, not blank" "$(cell 'website page' owner_module)" "(none)"
expect "the edit is in the detail"   "$(python3 -c "
import csv,sys
for r in csv.DictReader(open(sys.argv[1])):
    if r['label'] == 'edited list':
        print('yes' if 'arch edited in database' in r['detail'] else r['detail'])
        break" "$SCRIPT_DIR/.studio.csv")" "yes"

# --- #877: the 17.0+ automation join ------------------------------------------
expect "automation label is plain text" "$(cell 'base.automation:1' label)" "Notify on confirm"
# The model comes off ir_act_server.model_name; the old join raised
# UndefinedColumn before any of this could be read.
expect "automation model name"     "$(cell 'base.automation:1' model)" "sale.order"
expect "active automation is data" "$(cls 'base.automation:1')" "keep-as-data"

expect "version read from base module" "$(python3 -c "
import json,sys; print(json.loads(sys.argv[1])['odoo_version'])" "$out")" "17.0.1.3"
expect "rows are not dumped on stdout" "$(python3 -c "
import json,sys; print('rows' in json.loads(sys.argv[1]))" "$out")" "False"

rm -f "$SCRIPT_DIR/.studio.csv"
echo "{\"passed\": $pass, \"failed\": $fail}"
[ "$fail" -eq 0 ]
