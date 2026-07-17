#!/usr/bin/env python3
"""Host-side initializer for the central odoo-sdk tracker database (issue #369).

The tracker database is a single per-user SQLite file that holds every task's
events, the ``task_runs`` FSM, and the timesheet upload ledger. It is provisioned
ON THE HOST and bind-mounted into every dev container: the SDK inside the
container deliberately never creates it (a self-created DB would be
container-local and discarded on rebuild, silently splitting one person's billing
timeline), so this script is what brings the schema into existence.

Invoked by ``setup.sh`` / ``setup.ps1`` after they create the host state
directory. Give it the path to the ``tracker.db`` file to create/initialize:

    python3 scripts/init_tracker_db.py ~/.config/odoo-task-tracker/tracker.db

It is idempotent — a fresh DB is created with the ``CREATE ... IF NOT EXISTS``
schema, and an already-current DB is a harmless no-op that never touches existing
rows. An OLD-shape (pre-#452, non-STRICT) DB is REBUILT into the STRICT typed
schema before use (see :func:`migrate_schema`); if any row would fail the new
write-time validation the rebuild ABORTS with a clear listing and a non-zero exit,
leaving the DB untouched, so setup fails loudly instead of dropping data.

Stdlib-only by design: it runs on a bare host that has no ``odoo_sdk`` installed.
The schema below is a verbatim copy of ``odoo_sdk.state.db.SCHEMA_DDL`` and the
migration mirrors ``odoo_sdk.state.db.migrate_schema``; an SDK-side parity test
(``tests/test_state/test_init_script_parity.py``) provisions one database with
each and asserts their ``sqlite_master`` and schema version are identical, so the
two copies can never silently drift.
"""

import sqlite3
import sys
from pathlib import Path

#: Verbatim copy of ``odoo_sdk.state.db.SCHEMA_VERSION`` — the ``PRAGMA
#: user_version`` marker that tells an old-shape DB (``0``) from a current one.
SCHEMA_VERSION = 1

# VERBATIM copy of odoo_sdk.state.db.SCHEMA_DDL — kept identical by the parity
# test noted in the module docstring. Do not edit one without the other.
SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS task_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id      INTEGER NOT NULL,
    task_name    TEXT    NOT NULL,
    project_id   INTEGER NOT NULL,
    project_name TEXT    NOT NULL,
    state        TEXT    NOT NULL CHECK(state IN ('RUNNING', 'AWAITING_ANSWERS', 'STOPPED')),
    started_at   TEXT    NOT NULL CHECK(datetime(started_at) IS NOT NULL),
    stopped_at   TEXT             CHECK(stopped_at IS NULL OR datetime(stopped_at) IS NOT NULL),
    timesheet_id INTEGER,
    notes        TEXT    NOT NULL DEFAULT '[]' CHECK(json_valid(notes)),
    aborted_at   TEXT             CHECK(aborted_at IS NULL OR datetime(aborted_at) IS NOT NULL)
) STRICT;

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT    NOT NULL,
    timestamp   TEXT    NOT NULL CHECK(datetime(timestamp) IS NOT NULL),
    task_ids    TEXT    NOT NULL DEFAULT '[]' CHECK(json_valid(task_ids)),
    repo        TEXT    NOT NULL DEFAULT '',
    pr_num      INTEGER NOT NULL DEFAULT 0,
    branch      TEXT    NOT NULL DEFAULT '',
    subject     TEXT    NOT NULL DEFAULT '',
    payload     TEXT,
    external_id TEXT
) STRICT;

CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events (timestamp);

CREATE UNIQUE INDEX IF NOT EXISTS idx_events_external_id
    ON events(external_id) WHERE external_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS session_uploads (
    session_key  TEXT PRIMARY KEY,
    timesheet_id INTEGER NOT NULL,
    hours        REAL NOT NULL,
    uploaded_at  TEXT NOT NULL CHECK(datetime(uploaded_at) IS NOT NULL),
    task_id      TEXT,
    started_at   TEXT          CHECK(started_at IS NULL OR datetime(started_at) IS NOT NULL),
    ended_at     TEXT          CHECK(ended_at IS NULL OR datetime(ended_at) IS NOT NULL)
) STRICT;
"""

# Migration mirror of odoo_sdk.state.db (stdlib-only host copy). The tables,
# validation predicates, and rebuild pattern below are logically identical to the
# SDK's; the parity test asserts the resulting schema/version match, so behaviour
# cannot drift even though the code is duplicated.
_MIGRATION_TABLES = ("task_runs", "settings", "events", "session_uploads")

_ROW_VALIDATIONS = {
    "events": (
        ("datetime(timestamp) IS NULL", "invalid timestamp"),
        ("NOT json_valid(task_ids)", "invalid task_ids JSON"),
    ),
    "task_runs": (
        ("datetime(started_at) IS NULL", "invalid started_at"),
        ("stopped_at IS NOT NULL AND datetime(stopped_at) IS NULL", "invalid stopped_at"),
        ("aborted_at IS NOT NULL AND datetime(aborted_at) IS NULL", "invalid aborted_at"),
        ("NOT json_valid(notes)", "invalid notes JSON"),
        ("state NOT IN ('RUNNING', 'AWAITING_ANSWERS', 'STOPPED')", "invalid state"),
    ),
    "session_uploads": (
        ("datetime(uploaded_at) IS NULL", "invalid uploaded_at"),
        ("started_at IS NOT NULL AND datetime(started_at) IS NULL", "invalid started_at"),
        ("ended_at IS NOT NULL AND datetime(ended_at) IS NULL", "invalid ended_at"),
    ),
}

_ROW_KEY = {"events": "id", "task_runs": "id", "session_uploads": "session_key"}


class SchemaMigrationError(RuntimeError):
    """Raised when an old-shape tracker.db cannot be rebuilt into STRICT form.

    Mirrors ``odoo_sdk.state.db.SchemaMigrationError``: the rebuild aborts and
    lists every offending row rather than silently dropping data.
    """


def _ddl_statements():
    """Split :data:`SCHEMA_DDL` into its individual CREATE statements."""
    return [stmt.strip() for stmt in SCHEMA_DDL.split(";") if stmt.strip()]


def _schema_by_table():
    """Map each table to its ``(CREATE TABLE, [CREATE INDEX, ...])`` DDL."""
    tables = {}
    indexes = []
    for stmt in _ddl_statements():
        if stmt.upper().startswith("CREATE TABLE"):
            name = stmt.split("(", 1)[0].replace("IF NOT EXISTS", "").split()[-1]
            tables[name] = (stmt, [])
        else:
            target = stmt[stmt.upper().index(" ON ") + 4 :].split("(")[0].strip().split()[0]
            indexes.append((target, stmt))
    for target, stmt in indexes:
        tables[target][1].append(stmt)
    return tables


def _invalid_rows(conn, table):
    """Return a ``table[key=…]: reason`` line for every row failing validation."""
    checks = _ROW_VALIDATIONS.get(table)
    if not checks:
        return []
    key = _ROW_KEY[table]
    where = " OR ".join(f"({pred})" for pred, _ in checks)
    reason = " ".join(f"WHEN {pred} THEN {label!r}" for pred, label in checks)
    rows = conn.execute(
        f"SELECT {key}, CASE {reason} END FROM {table} WHERE {where}"
    ).fetchall()
    return [f"{table}[{key}={k}]: {label}" for k, label in rows]


def _stale_tables(conn):
    """Return the existing tables still on the pre-STRICT schema, in fixed order."""
    stale = []
    for table in _MIGRATION_TABLES:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        if row is not None and "STRICT" not in row[0].upper():
            stale.append(table)
    return stale


def _rebuild_table(conn, table, create_sql, index_sqls):
    """Rebuild one table into its STRICT form, preserving rows and ids."""
    old = f"{table}__pre_strict"
    conn.execute(f'ALTER TABLE "{table}" RENAME TO "{old}"')
    conn.execute(create_sql)
    conn.execute(f'INSERT INTO "{table}" SELECT * FROM "{old}"')
    conn.execute(f'DROP TABLE "{old}"')
    for index_sql in index_sqls:
        conn.execute(index_sql)


def migrate_schema(conn):
    """Rebuild any pre-#452 non-STRICT tables into the STRICT typed schema.

    A no-op when the DB is already at :data:`SCHEMA_VERSION` or holds no pre-STRICT
    tables. Otherwise offending rows are listed and the migration ABORTS with
    :class:`SchemaMigrationError` before any destructive rewrite; the rebuild runs
    in one transaction and is all-or-nothing.
    """
    if conn.execute("PRAGMA user_version").fetchone()[0] >= SCHEMA_VERSION:
        return
    stale = _stale_tables(conn)
    if not stale:
        return
    problems = [line for table in stale for line in _invalid_rows(conn, table)]
    if problems:
        raise SchemaMigrationError(
            "Cannot migrate tracker.db to the STRICT schema: "
            f"{len(problems)} row(s) fail the new write-time validation and would "
            "be lost. Fix or delete them, then re-run provisioning:\n"
            + "\n".join(problems)
        )
    schema = _schema_by_table()
    conn.commit()
    conn.execute("BEGIN")
    try:
        for table in stale:
            create_sql, index_sqls = schema[table]
            _rebuild_table(conn, table, create_sql, index_sqls)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def init_tracker_db(db_path: Path) -> None:
    """Create/upgrade ``db_path`` and apply the tracker schema, enabling WAL.

    The parent directory is expected to exist already (``setup.sh`` /
    ``setup.ps1`` create it from the manifest); this materializes the DB file (if
    absent), rebuilds any pre-STRICT tables from an older provisioning
    (:func:`migrate_schema`), applies the idempotent ``IF NOT EXISTS`` schema, and
    stamps :data:`SCHEMA_VERSION`. WAL is the persistent journal mode so the many
    cross-container writers a central DB now takes do not collide.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        migrate_schema(conn)
        conn.executescript(SCHEMA_DDL)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
    finally:
        conn.close()


def main(argv: list) -> int:
    if len(argv) != 2:
        print(
            "usage: init_tracker_db.py <path-to-tracker.db>",
            file=sys.stderr,
        )
        return 2
    db_path = Path(argv[1])
    parent = db_path.parent
    if not parent.exists():
        print(
            f"ERROR: parent directory {parent} does not exist; create the host "
            "state directory before initializing the tracker database.",
            file=sys.stderr,
        )
        return 1
    try:
        init_tracker_db(db_path)
    except SchemaMigrationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"ok  initialized tracker database at {db_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
