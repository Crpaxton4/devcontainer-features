import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from odoo_client.state.db import (
    LocalStateClient as TaskStateDB,
    assert_tracker_db_present,
    tracker_db_path,
)
from odoo_client.state.models import TrackerStateMissingError
from tests.support import make_state_db


def _tmp_db() -> TaskStateDB:
    return make_state_db()


class TestGetTaskEvents(unittest.TestCase):
    """Per-task audit event reads with inclusive window bounds (#626)."""

    def _add(self, db, task_ids, ts, subject="tool"):
        from odoo_client.state.models import EventRecord

        db.add_event(
            EventRecord(
                id=None,
                source="agent",
                timestamp=ts,
                task_ids=task_ids,
                repo="",
                subject=subject,
            )
        )

    def test_returns_only_the_tasks_events_in_order(self):
        db = _tmp_db()
        self._add(db, ["1"], datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc), "b")
        self._add(db, ["1"], datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc), "a")
        self._add(db, ["2"], datetime(2026, 8, 1, 9, 30, tzinfo=timezone.utc), "other")
        events = db.get_task_events("1")
        self.assertEqual([e.subject for e in events], ["a", "b"])

    def test_window_bounds_are_inclusive(self):
        db = _tmp_db()
        start = datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc)
        end = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)
        self._add(db, ["1"], start, "on-start")
        self._add(db, ["1"], end, "on-end")
        self._add(db, ["1"], datetime(2026, 8, 1, 8, 59, tzinfo=timezone.utc), "before")
        self._add(db, ["1"], datetime(2026, 8, 1, 10, 1, tzinfo=timezone.utc), "after")
        events = db.get_task_events("1", start=start, end=end)
        self.assertEqual([e.subject for e in events], ["on-start", "on-end"])

    def test_multi_task_event_matches_each_task_once(self):
        db = _tmp_db()
        ts = datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc)
        self._add(db, ["1", "2", "1"], ts, "shared")
        self.assertEqual(len(db.get_task_events("1")), 1)
        self.assertEqual(len(db.get_task_events("2")), 1)

    def test_no_events_returns_empty_list(self):
        db = _tmp_db()
        self.assertEqual(db.get_task_events("404"), [])


class TestChatterDedupe(unittest.TestCase):
    """The unique-index-backed chatter idempotency store (#631)."""

    def test_unseen_key_returns_none(self):
        db = _tmp_db()
        self.assertIsNone(db.get_chatter_dedupe(1, "missing"))

    def test_record_then_get_round_trips(self):
        db = _tmp_db()
        self.assertTrue(db.record_chatter_dedupe(1, "k", 55))
        self.assertEqual(db.get_chatter_dedupe(1, "k"), 55)

    def test_duplicate_record_keeps_first_mapping(self):
        # INSERT OR IGNORE against the unique index: the first mapping wins and
        # the loser is told so via the False return.
        db = _tmp_db()
        self.assertTrue(db.record_chatter_dedupe(1, "k", 55))
        self.assertFalse(db.record_chatter_dedupe(1, "k", 99))
        self.assertEqual(db.get_chatter_dedupe(1, "k"), 55)

    def test_keys_scoped_per_task(self):
        db = _tmp_db()
        db.record_chatter_dedupe(1, "k", 55)
        self.assertIsNone(db.get_chatter_dedupe(2, "k"))
        self.assertTrue(db.record_chatter_dedupe(2, "k", 66))
        self.assertEqual(db.get_chatter_dedupe(1, "k"), 55)
        self.assertEqual(db.get_chatter_dedupe(2, "k"), 66)

    def test_unique_index_enforced_at_sql_level(self):
        # The store is index-backed, not application-guessed: a raw duplicate
        # INSERT violates ``idx_chatter_dedupe_key``.
        db = _tmp_db()
        db.record_chatter_dedupe(1, "k", 55)
        with sqlite3.connect(str(db._db_path)) as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO chatter_dedupe "
                    "(task_id, dedupe_key, message_id, created_at) "
                    "VALUES (1, 'k', 99, '2026-07-17T12:00:00+00:00')"
                )


class TestTaskStateDBSettings(unittest.TestCase):
    def test_set_and_get_setting(self):
        db = _tmp_db()
        db.set_setting("employee_id", "42")
        self.assertEqual(db.get_setting("employee_id"), "42")

    def test_get_setting_returns_none_for_missing(self):
        db = _tmp_db()
        self.assertIsNone(db.get_setting("nonexistent"))

    def test_set_setting_upserts(self):
        db = _tmp_db()
        db.set_setting("k", "v1")
        db.set_setting("k", "v2")
        self.assertEqual(db.get_setting("k"), "v2")


class TestSchema(unittest.TestCase):
    def test_provisioned_db_carries_task_runs(self):
        """A schema-provisioned DB has ``task_runs`` (and no legacy table)."""
        db = _tmp_db()
        with sqlite3.connect(str(db._db_path)) as check:
            tables = {
                row[0]
                for row in check.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        self.assertIn("task_runs", tables)
        self.assertNotIn("task_sessions", tables)


class TestTrackerDbPath(unittest.TestCase):
    def test_central_db_is_tracker_db_under_state_root(self):
        """The central DB is ``<state-root>/tracker.db`` — no per-repo hash."""
        with patch.dict("os.environ", {"ODOO_TASK_TRACKER_DIR": "/tmp/tt-test"}):
            path = tracker_db_path()
        self.assertEqual(path, Path("/tmp/tt-test/tracker.db"))

    def test_explicit_root_overrides_env(self):
        self.assertEqual(
            tracker_db_path(Path("/some/root")), Path("/some/root/tracker.db")
        )


class TestMissingStateRaises(unittest.TestCase):
    def test_absent_db_raises_named_error_on_use(self):
        """Binding is inert; the first operation raises TrackerStateMissingError."""
        missing = Path(tempfile.mkdtemp()) / "absent" / "tracker.db"
        db = TaskStateDB(db_path=missing)  # construction never touches the FS
        with self.assertRaises(TrackerStateMissingError) as ctx:
            db.get_task_events("1")
        self.assertIn(str(missing), str(ctx.exception))
        # The SDK must NOT have created the file as a side effect.
        self.assertFalse(missing.exists())


class TestAssertTrackerDbPresent(unittest.TestCase):
    """The extracted existence check (#642) shared by ``_raw_connect`` and the
    up-front capability guard, so both report an absent DB identically."""

    def test_absent_explicit_path_raises_with_remedy(self):
        missing = Path(tempfile.mkdtemp()) / "absent" / "tracker.db"
        with self.assertRaises(TrackerStateMissingError) as ctx:
            assert_tracker_db_present(missing)
        message = str(ctx.exception)
        self.assertIn(str(missing), message)
        self.assertIn("setup.sh", message)
        # Checking must never materialize the file (#369).
        self.assertFalse(missing.exists())

    def test_present_explicit_path_passes(self):
        present = Path(tempfile.mkdtemp()) / "tracker.db"
        make_state_db(present)
        self.assertIsNone(assert_tracker_db_present(present))

    def test_defaults_to_tracker_db_path(self):
        """With no argument the central location is consulted, not the cwd."""
        root = Path(tempfile.mkdtemp()) / "state-root"
        with patch.dict("os.environ", {"ODOO_TASK_TRACKER_DIR": str(root)}):
            with self.assertRaises(TrackerStateMissingError) as ctx:
                assert_tracker_db_present()
        self.assertIn(str(root / "tracker.db"), str(ctx.exception))
        # No directory is created as a side effect of resolving the path (#369).
        self.assertFalse(root.exists())

    def test_message_matches_the_connect_time_error(self):
        """One definition, one message — a caller cannot tell the two apart."""
        missing = Path(tempfile.mkdtemp()) / "absent" / "tracker.db"
        with self.assertRaises(TrackerStateMissingError) as guard:
            assert_tracker_db_present(missing)
        with self.assertRaises(TrackerStateMissingError) as connect:
            TaskStateDB(db_path=missing).get_task_events("1")
        self.assertEqual(str(guard.exception), str(connect.exception))


if __name__ == "__main__":
    unittest.main()
