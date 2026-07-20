"""STRICT typed schema + write-time validation and the rebuild migration (#452).

The tracker DB moved from non-STRICT tables (which accept malformed data silently
and only error later inside a ``json_each``/``julianday`` reporting query) to
STRICT tables with CHECK constraints, so a bad write fails immediately. STRICT
cannot be added in place, so an old-shape DB is rebuilt by
:func:`odoo_sdk.state.db.migrate_schema`; these tests pin both the write-time
rejection and the rebuild/abort behaviour on real (never mocked) SQLite.
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from odoo_sdk.state.db import (
    SCHEMA_DDL,
    SCHEMA_VERSION,
    SchemaMigrationError,
    create_schema,
    migrate_schema,
)

# The pre-#452 non-STRICT schema, used to provision an old-shape DB the migration
# must upgrade. Column order matches SCHEMA_DDL so the rebuild's ``SELECT *`` copy
# lines up exactly.
_LEGACY_DDL = """
CREATE TABLE IF NOT EXISTS task_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL,
    task_name TEXT NOT NULL, project_id INTEGER NOT NULL, project_name TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('RUNNING', 'AWAITING_ANSWERS', 'STOPPED')),
    started_at TEXT NOT NULL, stopped_at TEXT, timesheet_id INTEGER,
    notes TEXT NOT NULL DEFAULT '[]', aborted_at TEXT
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, timestamp TEXT NOT NULL,
    task_ids TEXT NOT NULL DEFAULT '[]', repo TEXT NOT NULL DEFAULT '',
    pr_num INTEGER NOT NULL DEFAULT 0, branch TEXT NOT NULL DEFAULT '',
    subject TEXT NOT NULL DEFAULT '', payload TEXT, external_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events (timestamp);
CREATE UNIQUE INDEX IF NOT EXISTS idx_events_external_id
    ON events(external_id) WHERE external_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS session_uploads (
    session_key TEXT PRIMARY KEY, timesheet_id INTEGER NOT NULL, hours REAL NOT NULL,
    uploaded_at TEXT NOT NULL, task_id TEXT, started_at TEXT, ended_at TEXT
);
"""

#: The v1 STRICT schema, i.e. the shape a real user's ``tracker.db`` has on disk
#: after #452 but before #504 added the ``CLOSED`` state. Derived from the current
#: canonical DDL by dropping the ``CLOSED`` literal so it can never drift from the
#: real prior shape (STRICT tables, old three-state task_runs CHECK).
_V1_DDL = SCHEMA_DDL.replace(", 'CLOSED'", "")

_TS = "2026-07-17T12:00:00+00:00"


def _legacy_conn(with_rows: bool = True, corrupt: bool = False) -> sqlite3.Connection:
    """Return an in-memory connection on the OLD non-STRICT schema."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(_LEGACY_DDL)
    if with_rows:
        conn.execute(
            "INSERT INTO events (source, timestamp, task_ids, external_id) "
            "VALUES ('agent', ?, '[\"5\"]', 'ext-1')",
            (_TS,),
        )
        conn.execute(
            "INSERT INTO task_runs (task_id, task_name, project_id, project_name, "
            "state, started_at, notes) VALUES (5, 'T', 1, 'P', 'RUNNING', ?, '[]')",
            (_TS,),
        )
        conn.execute("INSERT INTO settings (key, value) VALUES ('k', 'v')")
        conn.execute(
            "INSERT INTO session_uploads (session_key, timesheet_id, hours, "
            "uploaded_at) VALUES ('s1', 9, 1.5, ?)",
            (_TS,),
        )
    if corrupt:
        conn.execute(
            "INSERT INTO events (source, timestamp, task_ids) "
            "VALUES ('agent', 'not-a-timestamp', '[]')"
        )
        conn.execute(
            "INSERT INTO events (source, timestamp, task_ids) "
            "VALUES ('agent', ?, 'not json')",
            (_TS,),
        )
    conn.commit()
    return conn


def _is_strict(conn: sqlite3.Connection, table: str) -> bool:
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()[0]
    return "STRICT" in sql.upper()


class TestWriteTimeValidation(unittest.TestCase):
    """Malformed rows are rejected at INSERT time, not later at query time."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript(SCHEMA_DDL)

    def tearDown(self):
        self.conn.close()

    def test_bad_timestamp_rejected_on_write(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO events (source, timestamp, task_ids) "
                "VALUES ('agent', 'not-a-timestamp', '[]')"
            )

    def test_bad_task_ids_json_rejected_on_write(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO events (source, timestamp, task_ids) "
                "VALUES ('agent', ?, 'not json')",
                (_TS,),
            )

    def test_bad_notes_json_rejected_on_write(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO task_runs (task_id, task_name, project_id, "
                "project_name, state, started_at, notes) "
                "VALUES (1, 'T', 1, 'P', 'RUNNING', ?, 'not json')",
                (_TS,),
            )

    def test_bad_started_at_rejected_on_write(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO task_runs (task_id, task_name, project_id, "
                "project_name, state, started_at) "
                "VALUES (1, 'T', 1, 'P', 'RUNNING', 'nope')"
            )

    def test_bad_upload_timestamp_rejected_on_write(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO session_uploads (session_key, timesheet_id, hours, "
                "uploaded_at) VALUES ('s', 1, 1.0, 'nope')"
            )

    def test_closed_state_accepted_on_write(self):
        # The terminal CLOSED state passes the widened task_runs CHECK (#504).
        self.conn.execute(
            "INSERT INTO task_runs (task_id, task_name, project_id, project_name, "
            "state, started_at, notes) VALUES (1, 'T', 1, 'P', 'CLOSED', ?, '[]')",
            (_TS,),
        )
        self.conn.commit()
        self.assertEqual(
            self.conn.execute(
                "SELECT state FROM task_runs WHERE task_id = 1"
            ).fetchone()[0],
            "CLOSED",
        )

    def test_unknown_state_still_rejected_on_write(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO task_runs (task_id, task_name, project_id, "
                "project_name, state, started_at, notes) "
                "VALUES (1, 'T', 1, 'P', 'BOGUS', ?, '[]')",
                (_TS,),
            )

    def test_valid_rows_still_insert(self):
        # The whole point: legitimate data is unaffected. Nullable timestamps stay
        # optional (NULL passes the CHECK), micro-second UTC isoformat is accepted.
        self.conn.execute(
            "INSERT INTO events (source, timestamp, task_ids) "
            "VALUES ('agent', ?, '[\"5\", \"6\"]')",
            ("2026-07-17T12:00:00.123456+00:00",),
        )
        self.conn.execute(
            "INSERT INTO task_runs (task_id, task_name, project_id, project_name, "
            "state, started_at, stopped_at, notes) "
            "VALUES (1, 'T', 1, 'P', 'STOPPED', ?, NULL, '[\"a note\"]')",
            (_TS,),
        )
        self.conn.commit()
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1
        )


class TestRebuildMigration(unittest.TestCase):
    """An old-shape DB is rebuilt into STRICT form, preserving valid data."""

    def test_migrate_upgrades_tables_and_preserves_rows(self):
        conn = _legacy_conn()
        for table in ("task_runs", "settings", "events", "session_uploads"):
            self.assertFalse(_is_strict(conn, table))

        migrate_schema(conn)

        for table in ("task_runs", "settings", "events", "session_uploads"):
            self.assertTrue(_is_strict(conn, table), f"{table} not STRICT")
        self.assertEqual(
            conn.execute("PRAGMA user_version").fetchone()[0], 0,
            "migrate_schema rebuilds tables; create_schema stamps the version",
        )
        # Rows survive with ids and values intact.
        self.assertEqual(
            conn.execute(
                "SELECT id, source, external_id FROM events"
            ).fetchall(),
            [(1, "agent", "ext-1")],
        )
        self.assertEqual(
            conn.execute("SELECT task_id, state FROM task_runs").fetchall(),
            [(5, "RUNNING")],
        )
        self.assertEqual(
            conn.execute("SELECT value FROM settings WHERE key = 'k'").fetchone()[0],
            "v",
        )
        # Indexes are recreated on the rebuilt events table.
        idx = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' "
                "AND tbl_name = 'events' AND sql IS NOT NULL"
            )
        }
        self.assertEqual(idx, {"idx_events_timestamp", "idx_events_external_id"})

    def test_write_validation_active_after_migration(self):
        conn = _legacy_conn()
        migrate_schema(conn)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO events (source, timestamp, task_ids) "
                "VALUES ('agent', 'bad', '[]')"
            )

    def test_autoincrement_continues_after_migration(self):
        conn = _legacy_conn()
        migrate_schema(conn)
        cur = conn.execute(
            "INSERT INTO events (source, timestamp, task_ids) "
            "VALUES ('agent', ?, '[]')",
            (_TS,),
        )
        self.assertEqual(cur.lastrowid, 2)

    def test_create_schema_migrates_and_stamps_version(self):
        conn = _legacy_conn()
        create_schema(conn)
        self.assertEqual(
            conn.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION
        )
        self.assertTrue(_is_strict(conn, "events"))
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1
        )

    def test_migrated_schema_matches_fresh_schema(self):
        migrated = _legacy_conn()
        create_schema(migrated)
        fresh = sqlite3.connect(":memory:")
        create_schema(fresh)

        def objects(conn):
            return sorted(
                conn.execute(
                    "SELECT type, name, tbl_name, sql FROM sqlite_master"
                ).fetchall()
            )

        self.assertEqual(objects(migrated), objects(fresh))

    def test_migration_is_idempotent(self):
        conn = _legacy_conn()
        create_schema(conn)
        before = sorted(
            conn.execute("SELECT type, name, sql FROM sqlite_master").fetchall()
        )
        create_schema(conn)  # second run: version already current, no-op
        after = sorted(
            conn.execute("SELECT type, name, sql FROM sqlite_master").fetchall()
        )
        self.assertEqual(before, after)
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1
        )

    def test_fresh_db_migrate_is_noop(self):
        conn = sqlite3.connect(":memory:")
        migrate_schema(conn)  # nothing to migrate; must not raise
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'"
            ).fetchone()[0],
            0,
        )


class TestMigrationAbort(unittest.TestCase):
    """Corrupt rows abort the migration and leave the DB untouched."""

    def test_corrupt_rows_abort_with_listing(self):
        conn = _legacy_conn(corrupt=True)
        with self.assertRaises(SchemaMigrationError) as ctx:
            migrate_schema(conn)
        msg = str(ctx.exception)
        self.assertIn("invalid timestamp", msg)
        self.assertIn("invalid task_ids JSON", msg)
        self.assertIn("events[id=", msg)

    def test_db_untouched_after_abort(self):
        conn = _legacy_conn(corrupt=True)
        with self.assertRaises(SchemaMigrationError):
            migrate_schema(conn)
        # Tables are still the old non-STRICT shape and every row is preserved.
        self.assertFalse(_is_strict(conn, "events"))
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 3
        )

    def test_rebuild_rolls_back_on_copy_failure(self):
        # A value STRICT rejects but pre-flight does not cover (a non-integer in an
        # INTEGER column) fails during the copy; the rebuild transaction rolls back
        # so the DB is left whole rather than half-migrated.
        conn = _legacy_conn(with_rows=False)
        conn.execute(
            "INSERT INTO events (source, timestamp, task_ids, pr_num) "
            "VALUES ('agent', ?, '[]', 1.5)",
            (_TS,),
        )
        conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            migrate_schema(conn)
        # events is still the old shape and its row survives (transaction rolled back).
        self.assertFalse(_is_strict(conn, "events"))
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1
        )

    def test_bad_state_row_reported(self):
        conn = _legacy_conn(with_rows=False)
        # A legacy CHECK let only known states in, but a hand-edited/corrupt DB
        # could still carry one; the migration reports it rather than crashing.
        conn.execute("DROP TABLE task_runs")
        conn.execute(
            "CREATE TABLE task_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "task_id INTEGER NOT NULL, task_name TEXT NOT NULL, "
            "project_id INTEGER NOT NULL, project_name TEXT NOT NULL, "
            "state TEXT NOT NULL, started_at TEXT NOT NULL, stopped_at TEXT, "
            "timesheet_id INTEGER, notes TEXT NOT NULL DEFAULT '[]', aborted_at TEXT)"
        )
        conn.execute(
            "INSERT INTO task_runs (task_id, task_name, project_id, project_name, "
            "state, started_at, notes) VALUES (1, 'T', 1, 'P', 'BOGUS', ?, '[]')",
            (_TS,),
        )
        conn.commit()
        with self.assertRaises(SchemaMigrationError) as ctx:
            migrate_schema(conn)
        self.assertIn("invalid state", str(ctx.exception))


class TestClosedStateMigration(unittest.TestCase):
    """A v1 STRICT DB gains CLOSED via a task_runs-only rebuild (#504).

    Unlike the #452 rebuild (which had to remake every table for STRICT), the
    #504 bump only widens ``task_runs``' CHECK, so only that table is stale — the
    other three STRICT tables are already current and are left untouched.
    """

    def _v1_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.executescript(_V1_DDL)
        conn.execute(
            "INSERT INTO task_runs (task_id, task_name, project_id, project_name, "
            "state, started_at, notes) VALUES (5, 'T', 1, 'P', 'STOPPED', ?, '[]')",
            (_TS,),
        )
        # Stamp the version the STRICT-but-pre-CLOSED schema shipped with, so
        # migrate_schema actually runs instead of early-returning.
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
        return conn

    def test_v1_is_strict_but_rejects_closed(self):
        conn = self._v1_conn()
        self.assertTrue(_is_strict(conn, "task_runs"))
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO task_runs (task_id, task_name, project_id, "
                "project_name, state, started_at, notes) "
                "VALUES (6, 'T', 1, 'P', 'CLOSED', ?, '[]')",
                (_TS,),
            )

    def test_only_task_runs_is_stale(self):
        from odoo_sdk.state.db import _stale_tables

        conn = self._v1_conn()
        self.assertEqual(_stale_tables(conn), ["task_runs"])

    def test_migrate_widens_check_and_preserves_rows(self):
        conn = self._v1_conn()
        migrate_schema(conn)
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='task_runs'"
        ).fetchone()[0]
        self.assertIn("CLOSED", sql)
        # The pre-existing STOPPED row (id included) survives the rebuild.
        self.assertEqual(
            conn.execute("SELECT id, task_id, state FROM task_runs").fetchall(),
            [(1, 5, "STOPPED")],
        )
        # The widened CHECK now admits a CLOSED write that was rejected at v1.
        conn.execute(
            "INSERT INTO task_runs (task_id, task_name, project_id, project_name, "
            "state, started_at, notes) VALUES (6, 'T', 1, 'P', 'CLOSED', ?, '[]')",
            (_TS,),
        )

    def test_create_schema_stamps_v2_from_v1(self):
        conn = self._v1_conn()
        create_schema(conn)
        self.assertEqual(
            conn.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION
        )

    def test_migrated_v1_matches_fresh_schema(self):
        migrated = self._v1_conn()
        create_schema(migrated)
        fresh = sqlite3.connect(":memory:")
        create_schema(fresh)

        def objects(conn):
            return sorted(
                conn.execute(
                    "SELECT type, name, tbl_name, sql FROM sqlite_master"
                ).fetchall()
            )

        self.assertEqual(objects(migrated), objects(fresh))


if __name__ == "__main__":
    unittest.main()
