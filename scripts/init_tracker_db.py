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

It is idempotent — every statement is ``CREATE ... IF NOT EXISTS`` — so re-running
it against an already-initialized database is a harmless no-op that never touches
existing rows.

Stdlib-only by design: it runs on a bare host that has no ``odoo_sdk`` installed.
The schema below is a verbatim copy of ``odoo_sdk.state.db.SCHEMA_DDL``; an
SDK-side parity test (``tests/test_state/test_init_script_parity.py``) provisions
one database with each and asserts their ``sqlite_master`` is identical, so the
two copies can never silently drift.
"""

import sqlite3
import sys
from pathlib import Path

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
    started_at   TEXT    NOT NULL,
    stopped_at   TEXT,
    timesheet_id INTEGER,
    notes        TEXT    NOT NULL DEFAULT '[]',
    aborted_at   TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT    NOT NULL,
    timestamp   TEXT    NOT NULL,
    task_ids    TEXT    NOT NULL DEFAULT '[]',
    repo        TEXT    NOT NULL DEFAULT '',
    pr_num      INTEGER NOT NULL DEFAULT 0,
    branch      TEXT    NOT NULL DEFAULT '',
    subject     TEXT    NOT NULL DEFAULT '',
    payload     TEXT,
    external_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events (timestamp);

CREATE UNIQUE INDEX IF NOT EXISTS idx_events_external_id
    ON events(external_id) WHERE external_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS session_uploads (
    session_key  TEXT PRIMARY KEY,
    timesheet_id INTEGER NOT NULL,
    hours        REAL NOT NULL,
    uploaded_at  TEXT NOT NULL,
    task_id      TEXT,
    started_at   TEXT,
    ended_at     TEXT
);
"""


def init_tracker_db(db_path: Path) -> None:
    """Create ``db_path`` and apply the tracker schema, enabling WAL.

    The parent directory is expected to exist already (``setup.sh`` /
    ``setup.ps1`` create it from the manifest); this materializes the DB file and
    applies the idempotent schema. WAL is set as the persistent journal mode so
    the many cross-container writers a central DB now takes do not collide.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA_DDL)
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
    init_tracker_db(db_path)
    print(f"ok  initialized tracker database at {db_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
