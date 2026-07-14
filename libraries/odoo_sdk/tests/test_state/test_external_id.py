"""Tests for the ``events.external_id`` dedupe column and migration (issue #328).

Covers the ``add_event_dedup`` idempotency primitive, ``add_event`` returning the
existing row on conflict, ``external_id`` round-tripping, and the guarded
``_migrate_events_external_id`` migration (old DB gains the column, new DB is
unaffected, repeated runs are no-ops).
"""

import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from odoo_sdk.state import EventRecord, LocalStateClient
from odoo_sdk.state.db import _migrate_events_external_id

UTC = timezone.utc


def _tmp_path() -> Path:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Path(tmp.name)


def _tmp_db() -> LocalStateClient:
    return LocalStateClient(db_path=_tmp_path())


def _event(ext_id=None, task="101") -> EventRecord:
    return EventRecord(
        id=None,
        source="commit",
        timestamp=datetime(2026, 6, 1, 9, 0, tzinfo=UTC),
        task_ids=[task],
        repo="owner/repo",
        subject="did work",
        external_id=ext_id,
    )


class TestExternalIdColumn(unittest.TestCase):
    def test_external_id_roundtrips(self) -> None:
        db = _tmp_db()
        stored = db.add_event(_event(ext_id="git:abc"))
        self.assertEqual(db.get_event(stored.id).external_id, "git:abc")

    def test_external_id_defaults_to_none(self) -> None:
        db = _tmp_db()
        stored = db.add_event(_event())
        self.assertIsNone(db.get_event(stored.id).external_id)


class TestAddEventDedup(unittest.TestCase):
    def test_first_insert_returns_true(self) -> None:
        db = _tmp_db()
        self.assertTrue(db.add_event_dedup(_event(ext_id="git:abc")))
        self.assertEqual(db.count_events(), 1)

    def test_duplicate_external_id_returns_false(self) -> None:
        db = _tmp_db()
        db.add_event_dedup(_event(ext_id="git:abc"))
        self.assertFalse(db.add_event_dedup(_event(ext_id="git:abc", task="999")))
        self.assertEqual(db.count_events(), 1)  # no second row written

    def test_null_external_ids_never_collide(self) -> None:
        db = _tmp_db()
        # The unique index is partial (WHERE external_id IS NOT NULL), so many
        # NULL-keyed events coexist.
        self.assertTrue(db.add_event_dedup(_event()))
        self.assertTrue(db.add_event_dedup(_event()))
        self.assertEqual(db.count_events(), 2)

    def test_add_event_returns_existing_row_on_conflict(self) -> None:
        db = _tmp_db()
        first = db.add_event(_event(ext_id="git:abc"))
        again = db.add_event(_event(ext_id="git:abc", task="999"))
        # The conflicting insert is ignored and the original row is returned.
        self.assertEqual(again.id, first.id)
        self.assertEqual(again.task_ids, ["101"])
        self.assertEqual(db.count_events(), 1)


class TestMigrationIdempotence(unittest.TestCase):
    def _legacy_events_db(self) -> Path:
        """Create a DB whose ``events`` table predates the external_id column."""
        path = _tmp_path()
        conn = sqlite3.connect(str(path))
        conn.executescript(
            "CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "source TEXT NOT NULL, timestamp TEXT NOT NULL, "
            "task_ids TEXT NOT NULL DEFAULT '[]', repo TEXT NOT NULL DEFAULT '', "
            "pr_num INTEGER NOT NULL DEFAULT 0, branch TEXT NOT NULL DEFAULT '', "
            "subject TEXT NOT NULL DEFAULT '', payload TEXT);"
        )
        conn.commit()
        conn.close()
        return path

    def _columns(self, path: Path) -> set:
        conn = sqlite3.connect(str(path))
        try:
            return {row[1] for row in conn.execute("PRAGMA table_info(events)")}
        finally:
            conn.close()

    def test_old_db_gains_column(self) -> None:
        path = self._legacy_events_db()
        self.assertNotIn("external_id", self._columns(path))
        conn = sqlite3.connect(str(path))
        _migrate_events_external_id(conn)
        conn.commit()
        conn.close()
        self.assertIn("external_id", self._columns(path))

    def test_migration_runs_twice_without_error(self) -> None:
        path = self._legacy_events_db()
        conn = sqlite3.connect(str(path))
        _migrate_events_external_id(conn)
        _migrate_events_external_id(conn)  # idempotent: no duplicate column/index
        conn.commit()
        conn.close()
        self.assertIn("external_id", self._columns(path))

    def test_new_db_unaffected(self) -> None:
        # A DB created through the normal schema already has the column; opening
        # it (which re-runs the migration) leaves it usable and dedupe-capable.
        db = _tmp_db()
        self.assertIn("external_id", self._columns(db._db_path))
        self.assertTrue(db.add_event_dedup(_event(ext_id="git:xyz")))

    def test_legacy_db_becomes_dedupe_capable(self) -> None:
        path = self._legacy_events_db()
        db = LocalStateClient(db_path=path)  # runs the migration on open
        self.assertTrue(db.add_event_dedup(_event(ext_id="git:abc")))
        self.assertFalse(db.add_event_dedup(_event(ext_id="git:abc")))


if __name__ == "__main__":
    unittest.main()
