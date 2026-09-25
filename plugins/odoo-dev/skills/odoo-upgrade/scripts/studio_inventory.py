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
                      Inactive views, and views a module ships whose arch was
                      edited in the database (kind `view-inline-edit`).

Every classification is a proposal. The column exists to sort a review, not to
replace one.

`populated` says whether a stored Studio field holds any data anywhere:
`populated`, `empty`, or `n/a` when the question is unanswerable (non-stored
field, no table, no column of its own). It is blank for every other kind.

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
    from psycopg2 import sql as pgsql
except ImportError:  # pragma: no cover - environment problem, not a code path
    print("psycopg2 is required (pip install psycopg2-binary)", file=sys.stderr)
    raise SystemExit(2)

CSV_COLUMNS = [
    "kind", "technical_name", "model", "label", "owner_module",
    "active", "classification", "populated", "detail",
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


def column_type(cr, table, column):
    cr.execute(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name = %s AND column_name = %s",
        (table, column),
    )
    row = cr.fetchone()
    return row["data_type"] if row else None


def rows_of(cr, sql, params=None):
    cr.execute(sql, params or ())
    return cr.fetchall()


def label_expr(cr, table, column, ref=None):
    """SQL expression yielding the plain text of a possibly-translated column.

    Since 16.0 every translated column (`name`, `field_description`, ...) is
    jsonb keyed by language code, so selecting it raw puts the literal
    {'en_US': 'Carrier Account'} in the CSV instead of the label a human reads.
    On <= 16 the same columns are plain varchar and must keep working, so the
    branch is on the column's actual type — the same schema probing the rest of
    this file uses for a major-version difference.

    Order of preference: en_US, then whatever language the database actually
    runs in, then the raw text. The last step means a label is never silently
    blank: a value nobody anticipated is shown as-is rather than dropped.
    """
    ref = ref or f"{table}.{column}"
    if column_type(cr, table, column) != "jsonb":
        return ref
    if table_exists(cr, "res_lang") and column_exists(cr, "res_lang", "active"):
        active_lang = ("(SELECT l.code FROM res_lang l "
                       "WHERE l.active ORDER BY l.id LIMIT 1)")
        return (f"COALESCE({ref} ->> 'en_US', {ref} ->> {active_lang}, "
                f"{ref} #>> '{{}}')")
    return f"COALESCE({ref} ->> 'en_US', {ref} #>> '{{}}')"


# What "holds data" means, per field type. A boolean defaulting to false and a
# numeric defaulting to zero are NOT NULL on every row, so a plain null test
# would report every field in the database as populated (see #929).
POPULATED_PREDICATES = {
    "boolean": "{col} IS TRUE",
    "integer": "{col} IS NOT NULL AND {col} <> 0",
    "float": "{col} IS NOT NULL AND {col} <> 0",
    "monetary": "{col} IS NOT NULL AND {col} <> 0",
    "char": "{col} IS NOT NULL AND {col}::text <> ''",
    "text": "{col} IS NOT NULL AND {col}::text <> ''",
    "html": "{col} IS NOT NULL AND {col}::text <> ''",
    "selection": "{col} IS NOT NULL AND {col}::text <> ''",
}
JSONB_POPULATED = ("{col} IS NOT NULL AND {col} <> '{{}}'::jsonb "
                   "AND {col} #>> '{{}}' <> ''")


def field_is_populated(cr, model, column, ttype):
    """'populated' / 'empty' / 'n/a' for one stored field.

    'n/a' is not 'empty': it means the question was not answerable here — the
    model has no table, or the field owns no column (a relational field stored
    in a join table, a binary stored as an attachment). Reporting those as
    empty would under-scope the upgrade, which is the whole point of the column.
    """
    # Odoo derives the table from the model name by replacing dots with
    # underscores; a _table override is rare enough that a missing table is
    # reported as unmeasurable rather than guessed at.
    table = (model or "").replace(".", "_")
    if not table or not table_exists(cr, table):
        return "n/a"
    if not column_exists(cr, table, column):
        return "n/a"
    if column_type(cr, table, column) == "jsonb":
        predicate = JSONB_POPULATED
    else:
        predicate = POPULATED_PREDICATES.get(ttype or "", "{col} IS NOT NULL")
    # Identifiers come from the database, so they are quoted by psycopg2 rather
    # than interpolated; {{}} in the predicates is a literal {} after .format().
    # LIMIT 1 inside the count: the answer is binary, so the scan stops at the
    # first row that holds something rather than counting a million of them.
    query = pgsql.SQL("SELECT COUNT(*) AS n FROM (SELECT 1 FROM {table} "
                      "WHERE " + predicate + " LIMIT 1) probe").format(
        table=pgsql.Identifier(table), col=pgsql.Identifier(column))
    try:
        cr.execute(query)
    except psycopg2.Error:
        # Autocommit means a failed statement is its own transaction, so one
        # unreadable table cannot poison the rest of the scan.
        return "n/a"
    return "populated" if cr.fetchone()["n"] else "empty"


def collect_fields(cr):
    """Manual fields. x_studio_* are Studio's; other x_* are hand-made."""
    out = []
    has_stored = column_exists(cr, "ir_model_fields", "store")
    stored = ", f.store" if has_stored else ""
    description = label_expr(cr, "ir_model_fields", "field_description",
                             "f.field_description")
    for r in rows_of(cr, f"""
        SELECT f.id, f.name, f.model, {description} AS field_description, f.ttype,
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
        # Only a stored field owns a column to ask about. Where ir_model_fields
        # predates the `store` column, a computed field is the non-stored one.
        is_stored = bool(r["store"]) if has_stored else not r["is_computed"]
        populated = (field_is_populated(cr, r["model"], r["name"], r["ttype"])
                     if is_stored else "n/a")
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
            # Whether the field holds anything is the first question asked of
            # every row: a field classified convert-to-code that is empty
            # everywhere costs a module and a migration for no data (#929).
            "populated": populated,
            "detail": detail,
        })
    return out


def collect_models(cr):
    out = []
    name = label_expr(cr, "ir_model", "name", "m.name")
    for r in rows_of(cr, f"""
        SELECT m.id, m.model, {name} AS name, d.module
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


def arch_edited_expr(cr):
    """SQL predicate: this view's arch was edited in the database.

    Three independent signals, any of which is enough (#930). Each is guarded
    on the column existing, because the set of columns ir_ui_view carries
    differs across the supported series and a missing one must narrow the
    predicate, not raise:

      1. arch_updated — Odoo's own record that the stored arch no longer
         matches the file the module ships (only meaningful with arch_fs set).
      2. ir_model_data.noupdate — how a UI edit protects itself from the next
         module update.
      3. a non-system writer after the owning module was last updated.

    A view matching any of these is overriding what its module ships, and the
    next update of that module rewrites the arch with no warning.
    """
    signals = []
    if column_exists(cr, "ir_ui_view", "arch_updated") and \
            column_exists(cr, "ir_ui_view", "arch_fs"):
        signals.append("(v.arch_updated AND COALESCE(v.arch_fs, '') <> '')")
    if column_exists(cr, "ir_model_data", "noupdate"):
        signals.append("d.noupdate = true")
    if column_exists(cr, "ir_ui_view", "write_uid") and \
            column_exists(cr, "ir_ui_view", "write_date") and \
            column_exists(cr, "ir_module_module", "write_date"):
        # uid 1 is the system user; anything above it is a person who opened
        # the editor. A write after the module's own last write is theirs.
        signals.append("(v.write_uid > 1 AND m.write_date IS NOT NULL "
                       "AND v.write_date > m.write_date)")
    return " OR ".join(signals) if signals else "false"


def collect_views(cr):
    """Studio-owned views, inactive views, and hand-edited module views.

    Three populations, each a different upgrade hazard: Studio's own views are
    client data to convert, an inactive view is an earlier upgrade casualty,
    and a module-owned view whose arch was edited in the database is the
    invisible one — it looks shipped, and the next module update reverts it.
    """
    out = []
    edited = arch_edited_expr(cr)
    name = label_expr(cr, "ir_ui_view", "name", "v.name")
    module_join = ("LEFT JOIN ir_module_module m ON m.name = d.module"
                   if table_exists(cr, "ir_module_module") else "")
    for r in rows_of(cr, f"""
        SELECT v.id, {name} AS name, v.model, v.type, v.active, v.inherit_id,
               d.module, ({edited}) AS arch_edited
        FROM ir_ui_view v
        LEFT JOIN ir_model_data d ON d.model = 'ir.ui.view' AND d.res_id = v.id
        {module_join}
        WHERE d.module = %s
           OR (v.active = false AND d.module IS NULL)
           OR (({edited}) AND COALESCE(d.module, '') <> %s)
        ORDER BY v.model, v.name
    """, (STUDIO_MODULE, STUDIO_MODULE)):
        studio = r["module"] == STUDIO_MODULE
        arch_edited = bool(r["arch_edited"])
        orphan = not r["active"] and not r["module"]
        # Kept apart from the plain view rows: these are not Studio's work and
        # not abandoned work, they are shipped views the database disagrees with.
        kind = "view-inline-edit" if arch_edited and not studio and not orphan \
            else "view"
        if not r["active"]:
            # An inactive view is either already-abandoned work or an earlier
            # upgrade casualty; either way nobody should port it without asking.
            classification = "review"
        elif studio:
            classification = "convert-to-code"
        elif arch_edited:
            classification = "review"
        else:
            classification = "keep-as-data"
        detail = ("inherited" if r["inherit_id"] else "primary") + \
                 f" {r['type'] or '?'}"
        if arch_edited and not studio:
            detail += ", arch edited in database"
        if not r["active"]:
            detail += ", INACTIVE"
        out.append({
            "kind": kind,
            "technical_name": f"view:{r['id']}",
            "model": r["model"] or "",
            "label": r["name"] or "",
            # The owning module is the whole point of an inline-edit row: it
            # names whose next update reverts the edit. A view with no xmlid at
            # all is reported as such rather than as a blank cell.
            "owner_module": r["module"] or
                            ("(none)" if kind == "view-inline-edit" else ""),
            "active": bool(r["active"]),
            "classification": classification,
            "detail": detail,
        })
    return out


def collect_automations(cr):
    """base.automation rows. Its storage changed across 16 -> 17."""
    if not table_exists(cr, "base_automation"):
        return []
    # Storage changed across 16 -> 17. Up to 16.0 base_automation points at its
    # server action through action_server_id; from 17.0 that column is gone and
    # ir_act_server points back with base_automation_id. The presence of the
    # old column is the version test.
    name = label_expr(cr, "ir_act_server", "name", "s.name")
    own_action = column_exists(cr, "base_automation", "action_server_id")
    if own_action:
        sql = f"""
            SELECT b.id, b.active, {name} AS name, s.model_name AS model,
                   b.trigger, d.module
            FROM base_automation b
            JOIN ir_act_server s ON s.id = b.action_server_id
            LEFT JOIN ir_model_data d ON d.model = 'base.automation' AND d.res_id = b.id
            ORDER BY s.model_name, s.name
        """
    else:
        # From 17.0 the link is the reverse one: ir_act_server carries
        # base_automation_id, and base_automation has no action_server_id at
        # all — joining on it raised UndefinedColumn, so the Studio inventory
        # could never run against a 17.0+ source database (#877).
        sql = f"""
            SELECT b.id, b.active, {name} AS name, s.model_name AS model,
                   b.trigger, d.module
            FROM base_automation b
            JOIN ir_act_server s ON s.base_automation_id = b.id
            LEFT JOIN ir_model_data d ON d.model = 'base.automation' AND d.res_id = b.id
            ORDER BY s.model_name, s.name
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
    name = label_expr(cr, "ir_act_server", "name", "s.name")
    for r in rows_of(cr, f"""
        SELECT s.id, {name} AS name, s.model_name, s.state, d.module
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
    for r in rows_of(cr, f"""
        SELECT c.id, c.active, {name} AS name, d.module
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
    name = label_expr(cr, "ir_act_report_xml", "name", "a.name")
    for r in rows_of(cr, f"""
        SELECT a.id, {name} AS name, a.model, a.report_name, d.module
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
