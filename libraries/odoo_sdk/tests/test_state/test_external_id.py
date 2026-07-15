"""Tests for the ``events.external_id`` dedupe column (issue #328).

Covers the ``add_event_dedup`` idempotency primitive, ``add_event`` returning the
existing row on conflict, ``external_id`` round-tripping, and that the base schema
(now the single canonical DDL, #369) carries the column and its partial unique
index so a freshly provisioned DB is immediately dedupe-capable.
"""

import sqlite3
import unittest
from datetime import datetime, timezone

from odoo_sdk.state import EventRecord, LocalStateClient
from tests.support import make_state_db

UTC = timezone.utc


def _tmp_db() -> LocalStateClient:
    return make_state_db()


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

    def test_base_schema_carries_column_and_partial_index(self) -> None:
        db = _tmp_db()
        conn = sqlite3.connect(str(db._db_path))
        try:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
            indexes = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                )
            }
        finally:
            conn.close()
        self.assertIn("external_id", columns)
        self.assertIn("idx_events_external_id", indexes)


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


if __name__ == "__main__":
    unittest.main()
