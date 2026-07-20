"""Parity gate between the host init script and the SDK's canonical schema (#369).

``scripts/init_tracker_db.py`` is stdlib-only and runs on a bare host with no
``odoo_sdk`` installed, so it embeds a verbatim copy of
:data:`odoo_sdk.state.db.SCHEMA_DDL`. This test provisions one database with the
init script and one with the SDK's :func:`create_schema` and asserts their
``sqlite_master`` is byte-for-byte identical, so the two DDL copies can never
silently drift and a container that connects to a host-provisioned DB always sees
exactly the schema the SDK expects.

The script also embeds a copy of :func:`odoo_sdk.state.db.migrate_schema` (#452).
A fresh DB never reaches that code — ``migrate_schema`` early-returns when there
is nothing stale — so comparing fresh databases alone leaves the whole rebuild
path unexercised, and a drift in the host copy would silently mis-migrate a real
user's pre-#452 ``tracker.db`` during ``setup.sh``/``setup.ps1``. The migration
parity tests below therefore provision an OLD-shape DB, run the host script over
it, and assert the migrated result is indistinguishable from a fresh one — same
schema, same version, same rows, same write-time validation.
"""

import contextlib
import importlib.util
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

from odoo_sdk.state.db import SCHEMA_DDL, SCHEMA_VERSION, create_schema

INIT_SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "init_tracker_db.py"

#: The pre-#452 non-STRICT schema, i.e. the shape a real user's ``tracker.db``
#: has on disk today. It is a frozen historical artefact (nothing can change it
#: retroactively), so it is spelled out here rather than shared with
#: ``test_strict_schema.py`` — this gate must not depend on another test module.
#: Column order matches SCHEMA_DDL so the rebuild's ``SELECT *`` copy lines up.
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

#: The v1 STRICT schema (post-#452, pre-#504): the shape a real user's
#: ``tracker.db`` has on disk after STRICT was adopted but before the ``CLOSED``
#: state widened the task_runs CHECK. Derived from the canonical DDL by dropping
#: the ``CLOSED`` literal so it tracks the true prior shape without a hand-copy.
_V1_DDL = SCHEMA_DDL.replace(", 'CLOSED'", "")

_TS = "2026-07-17T12:00:00+00:00"


def _load_init_script():
    spec = importlib.util.spec_from_file_location("init_tracker_db", INIT_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _schema_objects(db_path: Path) -> list:
    conn = sqlite3.connect(str(db_path))
    try:
        return sorted(
            conn.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_master"
            ).fetchall()
        )
    finally:
        conn.close()


def _user_version(db_path: Path) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


#: One events row carrying a DISTINCT value in every column, so a rebuild that
#: copies columns in the wrong order shows up as a mismatched tuple rather than
#: passing unnoticed.
_EVENT_ROW = (
    1,
    "agent",
    _TS,
    '["5"]',
    "acme/repo",
    42,
    "feat/x",
    "subject",
    "{}",
    "ext-1",
)


def _legacy_db(tmp: Path, corrupt: bool = False) -> Path:
    """Provision a pre-#452 (non-STRICT) tracker.db on disk and return its path."""
    path = tmp / "tracker.db"
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(_LEGACY_DDL)
        conn.execute(
            "INSERT INTO events (id, source, timestamp, task_ids, repo, pr_num, "
            "branch, subject, payload, external_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
            _EVENT_ROW,
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
        conn.commit()
    finally:
        conn.close()
    # An old-shape DB predates the version marker; leaving it at 0 is what makes
    # the host script's migrate_schema actually run instead of early-returning.
    assert _user_version(path) == 0
    return path


def _fresh_sdk_db(tmp: Path) -> Path:
    """Provision a reference DB with the SDK's canonical :func:`create_schema`."""
    path = tmp / "sdk.db"
    conn = sqlite3.connect(str(path))
    create_schema(conn)
    conn.commit()
    conn.close()
    return path


def _v1_db(tmp: Path) -> Path:
    """Provision a v1 STRICT (pre-#504, pre-CLOSED) tracker.db and return its path."""
    path = tmp / "tracker.db"
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(_V1_DDL)
        conn.execute(
            "INSERT INTO events (id, source, timestamp, task_ids, repo, pr_num, "
            "branch, subject, payload, external_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
            _EVENT_ROW,
        )
        conn.execute(
            "INSERT INTO task_runs (task_id, task_name, project_id, project_name, "
            "state, started_at, notes) VALUES (5, 'T', 1, 'P', 'STOPPED', ?, '[]')",
            (_TS,),
        )
        # The STRICT schema shipped with user_version 1; leaving it there is what
        # makes the host script's migrate_schema run the CLOSED-widening rebuild.
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
    finally:
        conn.close()
    assert _user_version(path) == 1
    return path


class TestInitScriptParity(unittest.TestCase):
    def test_init_script_exists(self):
        self.assertTrue(
            INIT_SCRIPT.is_file(), f"host init script missing at {INIT_SCRIPT}"
        )

    def test_ddl_is_verbatim_copy(self):
        init = _load_init_script()
        self.assertEqual(init.SCHEMA_DDL, SCHEMA_DDL)

    def test_schema_version_is_in_parity(self):
        init = _load_init_script()
        self.assertEqual(init.SCHEMA_VERSION, SCHEMA_VERSION)

    def test_resulting_schema_is_identical(self):
        init = _load_init_script()
        tmp = Path(tempfile.mkdtemp())
        from_script = tmp / "script.db"
        init.init_tracker_db(from_script)

        from_sdk = _fresh_sdk_db(tmp)

        self.assertEqual(_schema_objects(from_script), _schema_objects(from_sdk))
        # Both provisioners stamp the same schema version marker (#452).
        self.assertEqual(_user_version(from_script), SCHEMA_VERSION)
        self.assertEqual(_user_version(from_sdk), SCHEMA_VERSION)

    def test_provisioned_tables_are_strict(self):
        # STRICT lives in the sqlite_master ``sql`` compared above; assert it
        # explicitly so the write-time validation guarantee is pinned (#452).
        init = _load_init_script()
        path = Path(tempfile.mkdtemp()) / "tracker.db"
        init.init_tracker_db(path)
        conn = sqlite3.connect(str(path))
        try:
            table_sql = conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(len(table_sql), 4)
        for name, sql in table_sql:
            self.assertIn("STRICT", sql.upper(), f"{name} is not STRICT")

    def test_init_script_is_idempotent(self):
        init = _load_init_script()
        path = Path(tempfile.mkdtemp()) / "tracker.db"
        init.init_tracker_db(path)
        before = _schema_objects(path)
        init.init_tracker_db(path)  # re-run: no error, no schema change
        self.assertEqual(_schema_objects(path), before)


class TestMigrationParity(unittest.TestCase):
    """An OLD-shape DB migrated by the host script lands on the fresh schema.

    A fresh DB never enters the rebuild path, so these are the only tests that
    exercise the host copy of ``migrate_schema``/``_rebuild_table``.
    """

    def setUp(self):
        self.init = _load_init_script()
        self.tmp = Path(tempfile.mkdtemp())

    def test_migrate_schema_is_importable_from_the_script(self):
        # The host copy is what setup.sh/setup.ps1 actually calls; pin the names
        # the tests below (and the parity guarantee) depend on.
        self.assertTrue(callable(self.init.migrate_schema))
        self.assertTrue(issubclass(self.init.SchemaMigrationError, RuntimeError))

    def test_migrated_schema_matches_fresh_init(self):
        legacy = _legacy_db(self.tmp)
        self.init.init_tracker_db(legacy)

        fresh = _fresh_sdk_db(self.tmp)
        self.assertEqual(_schema_objects(legacy), _schema_objects(fresh))
        self.assertEqual(_user_version(legacy), SCHEMA_VERSION)

    def test_every_table_is_rebuilt_as_strict(self):
        # A table missing from the host's _MIGRATION_TABLES would survive the
        # migration in its old non-STRICT shape.
        legacy = _legacy_db(self.tmp)
        self.init.init_tracker_db(legacy)
        conn = sqlite3.connect(str(legacy))
        try:
            table_sql = conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(len(table_sql), 4)
        for name, sql in table_sql:
            self.assertIn("STRICT", sql.upper(), f"{name} was not migrated")

    def test_migration_preserves_rows_column_for_column(self):
        legacy = _legacy_db(self.tmp)
        self.init.init_tracker_db(legacy)
        conn = sqlite3.connect(str(legacy))
        try:
            self.assertEqual(
                conn.execute(
                    "SELECT id, source, timestamp, task_ids, repo, pr_num, branch, "
                    "subject, payload, external_id FROM events"
                ).fetchall(),
                [_EVENT_ROW],
            )
            self.assertEqual(
                conn.execute(
                    "SELECT task_id, state, started_at FROM task_runs"
                ).fetchall(),
                [(5, "RUNNING", _TS)],
            )
            self.assertEqual(
                conn.execute(
                    "SELECT value FROM settings WHERE key = 'k'"
                ).fetchone()[0],
                "v",
            )
            self.assertEqual(
                conn.execute(
                    "SELECT timesheet_id, hours FROM session_uploads WHERE "
                    "session_key = 's1'"
                ).fetchone(),
                (9, 1.5),
            )
        finally:
            conn.close()

    def test_write_validation_is_active_after_migration(self):
        # The point of #452: a mis-migrated table would keep accepting malformed
        # rows even though the DB is stamped as current.
        legacy = _legacy_db(self.tmp)
        self.init.init_tracker_db(legacy)
        conn = sqlite3.connect(str(legacy))
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO events (source, timestamp, task_ids) "
                    "VALUES ('agent', 'not-a-timestamp', '[]')"
                )
        finally:
            conn.close()

    def test_second_run_over_a_migrated_db_is_a_noop(self):
        legacy = _legacy_db(self.tmp)
        self.init.init_tracker_db(legacy)
        before = _schema_objects(legacy)
        self.init.init_tracker_db(legacy)
        self.assertEqual(_schema_objects(legacy), before)
        self.assertEqual(_user_version(legacy), SCHEMA_VERSION)

    def test_corrupt_rows_abort_and_leave_the_db_untouched(self):
        legacy = _legacy_db(self.tmp, corrupt=True)
        with self.assertRaises(self.init.SchemaMigrationError) as ctx:
            self.init.init_tracker_db(legacy)
        self.assertIn("invalid timestamp", str(ctx.exception))
        self.assertIn("events[id=", str(ctx.exception))
        # Nothing was rewritten: still old-shape, still unstamped, rows intact.
        conn = sqlite3.connect(str(legacy))
        try:
            sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'events'"
            ).fetchone()[0]
            self.assertNotIn("STRICT", sql.upper())
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 2
            )
        finally:
            conn.close()
        self.assertEqual(_user_version(legacy), 0)

    def test_main_reports_a_failed_migration_as_a_setup_error(self):
        # setup.sh/setup.ps1 must fail loudly rather than continue with a
        # half-provisioned tracker.db.
        legacy = _legacy_db(self.tmp, corrupt=True)
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = self.init.main(["init_tracker_db.py", str(legacy)])
        self.assertEqual(code, 1)
        self.assertIn("invalid timestamp", stderr.getvalue())


class TestClosedStateMigrationParity(unittest.TestCase):
    """The host script's v1→v2 rebuild matches a fresh v2 init (#504).

    Mirrors :class:`TestMigrationParity` for the newer schema step: a fresh DB
    early-returns from ``migrate_schema``, so only an OLD-shape DB exercises the
    host copy of the CLOSED-widening rebuild. A v1 STRICT DB (pre-CLOSED) is
    migrated by the host script and must be indistinguishable from a fresh v2 init.
    """

    def setUp(self):
        self.init = _load_init_script()
        self.tmp = Path(tempfile.mkdtemp())

    def test_migrated_v1_matches_fresh_init(self):
        v1 = _v1_db(self.tmp)
        self.init.init_tracker_db(v1)

        fresh = _fresh_sdk_db(self.tmp)
        self.assertEqual(_schema_objects(v1), _schema_objects(fresh))
        self.assertEqual(_user_version(v1), SCHEMA_VERSION)

    def test_migrated_task_runs_admits_closed(self):
        v1 = _v1_db(self.tmp)
        self.init.init_tracker_db(v1)
        conn = sqlite3.connect(str(v1))
        try:
            sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND "
                "name='task_runs'"
            ).fetchone()[0]
            self.assertIn("CLOSED", sql)
            # A CLOSED write is accepted post-migration; the STOPPED row survives.
            conn.execute(
                "INSERT INTO task_runs (task_id, task_name, project_id, "
                "project_name, state, started_at, notes) "
                "VALUES (6, 'T', 1, 'P', 'CLOSED', ?, '[]')",
                (_TS,),
            )
            conn.commit()
            self.assertEqual(
                conn.execute(
                    "SELECT task_id, state FROM task_runs ORDER BY task_id"
                ).fetchall(),
                [(5, "STOPPED"), (6, "CLOSED")],
            )
        finally:
            conn.close()

    def test_second_run_over_migrated_v1_is_a_noop(self):
        v1 = _v1_db(self.tmp)
        self.init.init_tracker_db(v1)
        before = _schema_objects(v1)
        self.init.init_tracker_db(v1)
        self.assertEqual(_schema_objects(v1), before)
        self.assertEqual(_user_version(v1), SCHEMA_VERSION)


if __name__ == "__main__":
    unittest.main()
