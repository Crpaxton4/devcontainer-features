#!/usr/bin/env python3
"""Enumerate Studio and other UI-created customizations in an Odoo database.

Studio work is invisible to a code inventory: it lives in database rows, not in
a git repo, so `module_inventory.py` cannot see any of it. It is also the part
of an upgrade most likely to break quietly — Studio views are client data, the
platform deactivates the ones that stop validating, and nothing in the codebase
records that they existed.

This script answers "what did they build in the UI, and what has to happen to
each of it before the upgrade". Read-only: it issues SELECTs and nothing else.

    python3 studio_inventory.py [--db NAME] [-o studio.json] [--csv studio.csv]

Connection comes from libpq environment (PGHOST/PGUSER/PGPASSWORD/PGDATABASE);
--db overrides PGDATABASE. Nothing is written to the database, ever.

Classification, per row:

    purge             Dead or superseded. Nothing to carry across.
    convert-to-code   Behaviour the customer depends on that should become a
                      real module before the upgrade: x_studio_* fields, studio
                      server actions, studio crons, studio reports.
    keep-as-data      Legitimately data, and migrates with the database:
                      automations on standard models, UI-created records.
    review            Not classifiable from the schema alone — a human looks.

Every classification is a proposal. The column exists to sort a review, not to
replace one.

Output JSON: {"db", "odoo_version", "counts": {...}, "rows": [...],
              "csv": path|null, "json": path|null}
Last stdout line is that JSON, per the house script contract.

Exit codes: 0 ok | 2 usage/connection | 3 not an Odoo database
"""

import argparse
import csv
import json
import os
import sys

try:
    import psycopg2
    import psycopg2.extras
except ImportError:  # pragma: no cover - environment problem, not a code path
    print("psycopg2 is required (pip install psycopg2-binary)", file=sys.stderr)
    raise SystemExit(2)

CSV_COLUMNS = [
    "kind", "technical_name", "model", "label", "owner_module",
    "active", "classification", "detail",
]

# Studio owns exactly one module name; everything it creates is registered under
# it in ir_model_data. That is the only reliable marker — a field called
# x_studio_foo can also be hand-written, and a manual field can predate Studio.
STUDIO_MODULE = "studio_customization"


def table_exists(cr, name):
    # RealDictCursor returns mappings, not tuples, so the column is named.
    cr.execute("SELECT to_regclass(%s) AS reg", (f"public.{name}",))
    return cr.fetchone()["reg"] is not None


def column_exists(cr, table, column):
    cr.execute(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = %s AND column_name = %s",
        (table, column),
    )
    return cr.fetchone() is not None


def rows_of(cr, sql, params=None):
    cr.execute(sql, params or ())
    return cr.fetchall()


def collect_fields(cr):
    """Manual fields. x_studio_* are Studio's; other x_* are hand-made."""
    out = []
    has_stored = column_exists(cr, "ir_model_fields", "store")
    stored = ", f.store" if has_stored else ""
    for r in rows_of(cr, f"""
        SELECT f.id, f.name, f.model, f.field_description, f.ttype,
               f.relation, f.compute IS NOT NULL AS is_computed{stored},
               d.module
        FROM ir_model_fields f
        LEFT JOIN ir_model_data d
               ON d.model = 'ir.model.fields' AND d.res_id = f.id
        WHERE f.state = 'manual'
        ORDER BY f.model, f.name
    """):
        studio = r["name"].startswith("x_studio")
        owner = r["module"] or ("" if not studio else STUDIO_MODULE)
        detail = r["ttype"] or ""
        if r["relation"]:
            detail += f" -> {r['relation']}"
        if r["is_computed"]:
            detail += " (computed)"
        out.append({
            "kind": "field",
            "technical_name": r["name"],
            "model": r["model"],
            "label": r["field_description"] or "",
            "owner_module": owner,
            "active": True,
            # A field holds customer data. It is never purged silently: it
            # becomes a real field in a real module, and a migration script
            # carries the column across (see references/migrations.md).
            "classification": "convert-to-code",
            "detail": detail,
        })
    return out


def collect_models(cr):
    out = []
    for r in rows_of(cr, """
        SELECT m.id, m.model, m.name, d.module
        FROM ir_model m
        LEFT JOIN ir_model_data d ON d.model = 'ir.model' AND d.res_id = m.id
        WHERE m.state = 'manual'
        ORDER BY m.model
    """):
        out.append({
            "kind": "model", "technical_name": r["model"], "model": r["model"],
            "label": r["name"] or "", "owner_module": r["module"] or "",
            "active": True, "classification": "convert-to-code",
            "detail": "UI-created model — needs a real model, and its table carried across",
        })
    return out


def collect_views(cr):
    """Studio-owned views, plus any inactive view (the upgrade casualty)."""
    out = []
    for r in rows_of(cr, """
        SELECT v.id, v.name, v.model, v.type, v.active, v.inherit_id, d.module
        FROM ir_ui_view v
        LEFT JOIN ir_model_data d ON d.model = 'ir.ui.view' AND d.res_id = v.id
        WHERE d.module = %s OR (v.active = false AND d.module IS NULL)
        ORDER BY v.model, v.name
    """, (STUDIO_MODULE,)):
        studio = r["module"] == STUDIO_MODULE
        out.append({
            "kind": "view",
            "technical_name": f"view:{r['id']}",
            "model": r["model"] or "",
            "label": r["name"] or "",
            "owner_module": r["module"] or "",
            "active": bool(r["active"]),
            # An inactive view is either already-abandoned work or an earlier
            # upgrade casualty; either way nobody should port it without asking.
            "classification": "review" if not r["active"] else
                              ("convert-to-code" if studio else "keep-as-data"),
            "detail": ("inherited" if r["inherit_id"] else "primary") +
                      f" {r['type'] or '?'}" + ("" if r["active"] else ", INACTIVE"),
        })
    return out


def collect_automations(cr):
    """base.automation rows. Its storage changed across 16 -> 17."""
    if not table_exists(cr, "base_automation"):
        return []
    # From 17.0 base_automation _inherits ir.actions.server and carries no name
    # of its own; before that it is a standalone table with its own columns.
    inherits_server = column_exists(cr, "base_automation", "action_server_id")
    if inherits_server:
        sql = """
            SELECT b.id, b.active, s.name, s.model_name AS model, b.trigger, d.module
            FROM base_automation b
            JOIN ir_act_server s ON s.id = b.action_server_id
            LEFT JOIN ir_model_data d ON d.model = 'base.automation' AND d.res_id = b.id
            ORDER BY s.model_name, s.name
        """
    else:
        sql = """
            SELECT b.id, b.active, s.name, m.model AS model, b.trigger, d.module
            FROM base_automation b
            JOIN ir_act_server s ON s.id = b.action_server_id
            JOIN ir_model m ON m.id = s.model_id
            LEFT JOIN ir_model_data d ON d.model = 'base.automation' AND d.res_id = b.id
            ORDER BY m.model, s.name
        """
    out = []
    for r in rows_of(cr, sql):
        out.append({
            "kind": "automation",
            "technical_name": f"base.automation:{r['id']}",
            "model": r["model"] or "",
            "label": r["name"] or "",
            "owner_module": r["module"] or "",
            "active": bool(r["active"]),
            "classification": "purge" if not r["active"] else "keep-as-data",
            "detail": f"trigger={r['trigger'] or '?'}",
        })
    return out


def collect_server_actions(cr):
    """Server actions and crons that no module owns, or that Studio owns."""
    out = []
    # From 17.0 ir.cron _inherits ir.actions.server, so every cron owns a server
    # action row. Reporting those here as well would list each scheduled action
    # twice, under two different classifications — and the cron row is the one
    # that knows whether it is still active.
    cron_backed = "SELECT ir_actions_server_id FROM ir_cron" \
        if table_exists(cr, "ir_cron") and column_exists(cr, "ir_cron", "ir_actions_server_id") \
        else "SELECT NULL::integer"
    for r in rows_of(cr, f"""
        SELECT s.id, s.name, s.model_name, s.state, d.module
        FROM ir_act_server s
        LEFT JOIN ir_model_data d ON d.model = 'ir.actions.server' AND d.res_id = s.id
        WHERE (d.id IS NULL OR d.module = %s)
          AND s.id NOT IN ({cron_backed})
        ORDER BY s.name
    """, (STUDIO_MODULE,)):
        out.append({
            "kind": "server_action",
            "technical_name": f"ir.actions.server:{r['id']}",
            "model": r["model_name"] or "",
            "label": r["name"] or "",
            "owner_module": r["module"] or "(none)",
            "active": True,
            # Unowned Python in the database is the worst thing to carry across
            # an upgrade: no review, no tests, no version control.
            "classification": "convert-to-code" if r["state"] == "code" else "review",
            "detail": f"state={r['state'] or '?'}",
        })
    for r in rows_of(cr, """
        SELECT c.id, c.active, s.name, d.module
        FROM ir_cron c
        JOIN ir_act_server s ON s.id = c.ir_actions_server_id
        LEFT JOIN ir_model_data d ON d.model = 'ir.cron' AND d.res_id = c.id
        WHERE d.id IS NULL OR d.module = %s
        ORDER BY s.name
    """, (STUDIO_MODULE,)):
        out.append({
            "kind": "cron", "technical_name": f"ir.cron:{r['id']}", "model": "",
            "label": r["name"] or "", "owner_module": r["module"] or "(none)",
            "active": bool(r["active"]),
            "classification": "purge" if not r["active"] else "convert-to-code",
            "detail": "UI-created scheduled action",
        })
    return out


def collect_reports(cr):
    out = []
    for r in rows_of(cr, """
        SELECT a.id, a.name, a.model, a.report_name, d.module
        FROM ir_act_report_xml a
        LEFT JOIN ir_model_data d ON d.model = 'ir.actions.report' AND d.res_id = a.id
        WHERE d.id IS NULL OR d.module = %s
        ORDER BY a.model, a.name
    """, (STUDIO_MODULE,)):
        out.append({
            "kind": "report", "technical_name": r["report_name"] or f"report:{r['id']}",
            "model": r["model"] or "", "label": r["name"] or "",
            "owner_module": r["module"] or "(none)", "active": True,
            "classification": "convert-to-code",
            "detail": "UI-created report — its QWeb template is database-only",
        })
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", help="database name (default: $PGDATABASE)")
    ap.add_argument("-o", "--json", dest="json_out", help="write the full JSON here")
    ap.add_argument("--csv", dest="csv_out", help="write the rows as CSV here")
    args = ap.parse_args()

    dbname = args.db or os.environ.get("PGDATABASE")
    if not dbname:
        print("no database: pass --db or set PGDATABASE", file=sys.stderr)
        raise SystemExit(2)

    try:
        conn = psycopg2.connect(dbname=dbname)
    except psycopg2.Error as exc:
        print(f"could not connect to {dbname}: {str(exc).strip()}", file=sys.stderr)
        raise SystemExit(2)

    # Read-only by construction, not by convention: the connection cannot write
    # even if a query below were changed to try.
    conn.set_session(readonly=True, autocommit=True)
    cr = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    if not table_exists(cr, "ir_model_fields"):
        print(f"{dbname} is not an Odoo database (no ir_model_fields)", file=sys.stderr)
        raise SystemExit(3)

    version = None
    if table_exists(cr, "ir_module_module"):
        cr.execute("SELECT latest_version FROM ir_module_module WHERE name = 'base'")
        row = cr.fetchone()
        version = row["latest_version"] if row else None

    rows = (collect_fields(cr) + collect_models(cr) + collect_views(cr)
            + collect_automations(cr) + collect_server_actions(cr) + collect_reports(cr))

    counts = {}
    for r in rows:
        counts[r["kind"]] = counts.get(r["kind"], 0) + 1
        key = f"classification:{r['classification']}"
        counts[key] = counts.get(key, 0) + 1
    counts["total"] = len(rows)

    if args.csv_out:
        with open(args.csv_out, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
            writer.writeheader()
            for r in rows:
                writer.writerow({k: r.get(k, "") for k in CSV_COLUMNS})

    payload = {
        "db": dbname, "odoo_version": version, "counts": counts, "rows": rows,
        "csv": args.csv_out, "json": args.json_out,
    }
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)

    # Rows are in the file, not on stdout: a 400-row array is not a summary.
    summary = dict(payload)
    summary.pop("rows")
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
