#!/usr/bin/env python3
"""Enumerate Studio and other UI-created customizations in an Odoo database.

Studio work is invisible to a code inventory: it lives in database rows, not in
a git repo, so `module_inventory.py` cannot see any of it. It is also the part
of an upgrade most likely to break quietly — Studio views are client data, the
platform deactivates the ones that stop validating, and nothing in the codebase
records that they existed.

This script answers "what did they build in the UI, and what has to happen to
each of it before the upgrade". Read-only: it issues SELECTs and nothing else.

    python3 studio_inventory.py [--db NAME] [--ssh USER@HOST] [--artifacts DIR]
                                [-o studio.json] [--csv studio.csv]

Two transports, one set of queries:

  local   psycopg2 against a database this machine can reach. Connection comes
          from the libpq environment (PGHOST/PGUSER/PGPASSWORD/PGDATABASE);
          --db overrides PGDATABASE.
  --ssh   the same SQL, run through `psql` on the remote host over ssh. The
          source database of an upgrade normally lives on a staging or odoo.sh
          build and is reachable from nowhere else, and this path needs no
          psycopg2 — on either side. Every statement is preceded by
          `SET default_transaction_read_only = on`, so the session cannot write
          even if a query below were changed to try.

Nothing is written to the database, ever, on either transport. Nothing is copied
to the remote host either; if a later change ever needs to, use `scp -O` — the
hosts this runs against have no sftp subsystem.

--artifacts DIR is the task artifacts directory. Given it, the outputs default to
DIR/inventory/studio.json and DIR/inventory/studio.csv (the directory is created),
because an inventory written to /tmp or to a session scratchpad is a deliverable
nobody will find later. An explicit -o or --csv still wins.

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

Output JSON: {"db", "ssh": host|null, "odoo_version", "counts": {...},
              "rows": [...], "csv": path|null, "json": path|null}
Last stdout line is that JSON, per the house script contract.

Exit codes: 0 ok | 2 usage/connection | 3 not an Odoo database
"""

import argparse
import csv
import json
import os
import shlex
import subprocess
import sys

CSV_COLUMNS = [
    "kind", "technical_name", "model", "label", "owner_module",
    "active", "classification", "detail",
]

# Studio owns exactly one module name; everything it creates is registered under
# it in ir_model_data. That is the only reliable marker — a field called
# x_studio_foo can also be hand-written, and a manual field can predate Studio.
STUDIO_MODULE = "studio_customization"


class SshQueryError(RuntimeError):
    """A query, or the ssh call carrying it, came back non-zero."""


def connect_local(dbname):
    """psycopg2 cursor on a database this machine can reach.

    The import is here rather than at module scope so that `--ssh`, which needs
    no driver at all, still runs on a machine that has none installed.
    """
    try:
        import psycopg2
        import psycopg2.extras
    except ImportError:  # pragma: no cover - environment problem, not a code path
        print("psycopg2 is required for a local connection "
              "(pip install psycopg2-binary) — or read the remote database with "
              "--ssh user@host", file=sys.stderr)
        raise SystemExit(2)

    try:
        conn = psycopg2.connect(dbname=dbname)
    except psycopg2.Error as exc:
        print(f"could not connect to {dbname}: {str(exc).strip()}", file=sys.stderr)
        raise SystemExit(2)

    # Read-only by construction, not by convention: the connection cannot write
    # even if a query below were changed to try.
    conn.set_session(readonly=True, autocommit=True)
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


def sql_literal(value):
    """Render one query parameter as a SQL literal.

    psycopg2 sends parameters beside the statement; psql reading a script on
    stdin has no such channel, so the remote transport renders them into the
    text. The only parameters this script passes are its own constants, and the
    quoting is still done properly: a helper that is safe only for today's
    callers is a trap for tomorrow's.
    """
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def render_sql(sql, params):
    """Substitute %s placeholders with literals, left to right."""
    out, rest = [], sql
    for param in params:
        head, sep, rest = rest.partition("%s")
        if not sep:
            raise ValueError("more parameters than placeholders")
        out.append(head)
        out.append(sql_literal(param))
    out.append(rest)
    text = "".join(out)
    if "%s" in text:
        raise ValueError("fewer parameters than placeholders")
    return text


class SshCursor:
    """Cursor-shaped object that runs the same SQL through psql over ssh.

    It implements the three methods the collectors use — `execute`, `fetchone`,
    `fetchall` — and hands back dict rows, so a collector cannot tell the two
    transports apart and there is exactly one copy of every SQL string. Each
    statement is one psql invocation reading the script on stdin, prefixed with
    `SET default_transaction_read_only = on`: the remote session is read-only
    for the same reason the local connection is. Rows come back as one
    `row_to_json` object per line, which keeps the booleans, nulls and integers
    a delimited dump would flatten into strings.
    """

    READ_ONLY = "SET default_transaction_read_only = on;\n"

    def __init__(self, host, dbname):
        self.host = host
        self.dbname = dbname
        self._rows = []

    def _run(self, script):
        remote = " ".join(shlex.quote(a) for a in (
            "psql", "-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1",
            "-d", self.dbname, "-f", "-",
        ))
        proc = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", self.host, remote],
            input=script, capture_output=True, text=True, check=False,
        )
        if proc.returncode != 0:
            detail = proc.stderr.strip() or f"exit {proc.returncode}"
            raise SshQueryError(detail)
        return proc.stdout

    def execute(self, sql, params=None):
        statement = render_sql(sql, tuple(params or ()))
        script = f"{self.READ_ONLY}SELECT row_to_json(t) FROM (\n{statement}\n) t;\n"
        self._rows = [json.loads(line) for line in self._run(script).splitlines()
                      if line.strip()]

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


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
    ap.add_argument("--ssh", metavar="USER@HOST",
                    help="read the database through psql on this host over ssh, "
                         "read-only; needs no psycopg2 on either side")
    ap.add_argument("--artifacts", metavar="DIR",
                    help="task artifacts directory; -o and --csv then default to "
                         "DIR/inventory/studio.json and DIR/inventory/studio.csv")
    ap.add_argument("-o", "--json", dest="json_out", help="write the full JSON here")
    ap.add_argument("--csv", dest="csv_out", help="write the rows as CSV here")
    args = ap.parse_args()

    dbname = args.db or os.environ.get("PGDATABASE")
    if not dbname:
        print("no database: pass --db or set PGDATABASE", file=sys.stderr)
        raise SystemExit(2)

    json_out, csv_out = args.json_out, args.csv_out
    if args.artifacts:
        # The inventory is a deliverable; a scratch directory is the wrong
        # durability class for one. Default it beside the task's other artifacts.
        workdir = os.path.join(os.path.abspath(args.artifacts), "inventory")
        try:
            os.makedirs(workdir, exist_ok=True)
        except OSError as exc:
            print(f"could not create {workdir}: {exc}", file=sys.stderr)
            raise SystemExit(2)
        json_out = json_out or os.path.join(workdir, "studio.json")
        csv_out = csv_out or os.path.join(workdir, "studio.csv")

    if args.ssh:
        cr = SshCursor(args.ssh, dbname)
        try:
            # Prove the transport before the collectors do: an unreachable host
            # is the failure this script is most often asked to survive, and
            # surviving it silently is how a Studio inventory goes missing.
            cr.execute("SELECT current_database() AS db")
        except SshQueryError as exc:
            print(f"could not read {dbname} on {args.ssh}: {exc}", file=sys.stderr)
            raise SystemExit(2)
    else:
        cr = connect_local(dbname)

    try:
        if not table_exists(cr, "ir_model_fields"):
            print(f"{dbname} is not an Odoo database (no ir_model_fields)",
                  file=sys.stderr)
            raise SystemExit(3)

        version = None
        if table_exists(cr, "ir_module_module"):
            cr.execute("SELECT latest_version FROM ir_module_module WHERE name = 'base'")
            row = cr.fetchone()
            version = row["latest_version"] if row else None

        rows = (collect_fields(cr) + collect_models(cr) + collect_views(cr)
                + collect_automations(cr) + collect_server_actions(cr)
                + collect_reports(cr))
    except SshQueryError as exc:
        print(f"query failed on {args.ssh}: {exc}", file=sys.stderr)
        raise SystemExit(2)

    counts = {}
    for r in rows:
        counts[r["kind"]] = counts.get(r["kind"], 0) + 1
        key = f"classification:{r['classification']}"
        counts[key] = counts.get(key, 0) + 1
    counts["total"] = len(rows)

    if csv_out:
        with open(csv_out, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
            writer.writeheader()
            for r in rows:
                writer.writerow({k: r.get(k, "") for k in CSV_COLUMNS})

    payload = {
        "db": dbname, "ssh": args.ssh, "odoo_version": version, "counts": counts,
        "rows": rows, "csv": csv_out, "json": json_out,
    }
    if json_out:
        with open(json_out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)

    # Rows are in the file, not on stdout: a 400-row array is not a summary.
    summary = dict(payload)
    summary.pop("rows")
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
