import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from odoo_sdk.state import EventRecord, LocalStateClient, SessionWindow

UTC = timezone.utc


def _tmp_db() -> LocalStateClient:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return LocalStateClient(db_path=Path(tmp.name))


def _event(minute: int, task="101", source="commit") -> EventRecord:
    return EventRecord(
        id=None,
        source=source,
        timestamp=datetime(2026, 6, 1, 9, minute, tzinfo=UTC),
        task_ids=[task],
        repo="owner/repo",
        pr_num=0,
        branch="main",
        subject="did work",
        payload={"pr_title": "T", "pr_body": "B"},
    )


def _window(task="101") -> SessionWindow:
    return SessionWindow(
        id=None,
        task_id=task,
        repo="owner/repo",
        started_at=datetime(2026, 6, 1, 9, 0, tzinfo=UTC),
        ended_at=datetime(2026, 6, 1, 9, 30, tzinfo=UTC),
    )


class TestEventStore(unittest.TestCase):
    def test_add_and_get_event_roundtrips(self):
        db = _tmp_db()
        stored = db.add_event(_event(0))
        self.assertIsNotNone(stored.id)
        fetched = db.get_event(stored.id)
        self.assertEqual(fetched.task_ids, ["101"])
        self.assertEqual(fetched.payload, {"pr_title": "T", "pr_body": "B"})

    def test_get_missing_event_returns_none(self):
        self.assertIsNone(_tmp_db().get_event(999))

    def test_get_events_ordered_and_range_bounded(self):
        db = _tmp_db()
        db.add_event(_event(30))
        db.add_event(_event(0))
        events = db.get_events()
        self.assertEqual([e.timestamp.minute for e in events], [0, 30])
        bounded = db.get_events(
            start=datetime(2026, 6, 1, 9, 10, tzinfo=UTC),
            end=datetime(2026, 6, 1, 9, 45, tzinfo=UTC),
        )
        self.assertEqual(len(bounded), 1)

    def test_null_payload_roundtrips(self):
        db = _tmp_db()
        rec = _event(0)
        rec.payload = None
        stored = db.add_event(rec)
        self.assertIsNone(db.get_event(stored.id).payload)


class TestSessionWindowStore(unittest.TestCase):
    def test_add_and_get_window(self):
        db = _tmp_db()
        stored = db.add_session_window(_window())
        self.assertEqual(db.get_session_window(stored.id).task_id, "101")
        self.assertEqual(stored.duration_seconds, 1800)

    def test_get_missing_window_returns_none(self):
        self.assertIsNone(_tmp_db().get_session_window(999))

    def test_range_query_and_clear(self):
        db = _tmp_db()
        db.add_session_window(_window("101"))
        db.add_session_window(_window("102"))
        self.assertEqual(len(db.get_session_windows()), 2)
        bounded = db.get_session_windows(
            start=datetime(2026, 6, 1, 8, tzinfo=UTC),
            end=datetime(2026, 6, 1, 10, tzinfo=UTC),
        )
        self.assertEqual(len(bounded), 2)
        db.clear_session_windows()
        self.assertEqual(db.get_session_windows(), [])

    def test_coexists_with_fsm_store(self):
        db = _tmp_db()
        db.add_event(_event(0))
        session = db.create_session(1, "Task 1", 10, "Proj", timesheet_id=5)
        self.assertIsNotNone(session.id)
        self.assertEqual(len(db.get_events()), 1)


class TestEventSessionLink(unittest.TestCase):
    def test_session_id_roundtrips(self):
        db = _tmp_db()
        window = db.add_session_window(_window())
        rec = _event(0)
        rec.session_id = window.id
        stored = db.add_event(rec)
        self.assertEqual(db.get_event(stored.id).session_id, window.id)

    def test_set_and_clear_event_session(self):
        db = _tmp_db()
        window = db.add_session_window(_window())
        event = db.add_event(_event(0))
        self.assertIsNone(event.session_id)
        db.set_event_session(event.id, window.id)
        self.assertEqual(db.get_event(event.id).session_id, window.id)
        db.set_event_session(event.id, None)
        self.assertIsNone(db.get_event(event.id).session_id)

    def test_get_events_for_session(self):
        db = _tmp_db()
        window = db.add_session_window(_window())
        e1 = db.add_event(_event(0))
        e2 = db.add_event(_event(20))
        db.set_event_session(e1.id, window.id)
        db.set_event_session(e2.id, window.id)
        db.add_event(_event(40))  # unlinked
        linked = db.get_events_for_session(window.id)
        self.assertEqual([e.id for e in linked], [e1.id, e2.id])

    def test_deleting_session_nulls_links(self):
        # The FK is ON DELETE SET NULL: deleting a session must not orphan links.
        db = _tmp_db()
        window = db.add_session_window(_window())
        event = db.add_event(_event(0))
        db.set_event_session(event.id, window.id)
        db.delete_session_window(window.id)
        self.assertIsNone(db.get_event(event.id).session_id)

    def test_clearing_windows_nulls_links(self):
        db = _tmp_db()
        window = db.add_session_window(_window())
        event = db.add_event(_event(0))
        db.set_event_session(event.id, window.id)
        db.clear_session_windows()
        self.assertIsNone(db.get_event(event.id).session_id)


class TestSessionWindowMutation(unittest.TestCase):
    def test_update_session_window_in_place(self):
        db = _tmp_db()
        window = db.add_session_window(_window())
        window.ended_at = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
        updated = db.update_session_window(window)
        self.assertEqual(updated.id, window.id)
        self.assertEqual(db.get_session_window(window.id).ended_at, window.ended_at)

    def test_update_requires_id(self):
        with self.assertRaises(ValueError):
            _tmp_db().update_session_window(_window())


class TestSessionOverlapQuery(unittest.TestCase):
    def _win(self, task, start_h, end_h, repo="owner/repo", strategy="development"):
        return SessionWindow(
            id=None,
            task_id=task,
            repo=repo,
            started_at=datetime(2026, 6, 1, start_h, tzinfo=UTC),
            ended_at=datetime(2026, 6, 1, end_h, tzinfo=UTC),
            strategy_name=strategy,
        )

    def test_overlap_returns_whole_sessions(self):
        db = _tmp_db()
        # Session spans 08:00-12:00; a query for 10:00-11:00 must return it whole.
        db.add_session_window(self._win("101", 8, 12))
        found = db.get_sessions_overlapping(
            datetime(2026, 6, 1, 10, tzinfo=UTC),
            datetime(2026, 6, 1, 11, tzinfo=UTC),
        )
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].started_at.hour, 8)  # true, unclipped bounds
        self.assertEqual(found[0].ended_at.hour, 12)

    def test_non_overlapping_excluded(self):
        db = _tmp_db()
        db.add_session_window(self._win("101", 8, 9))
        found = db.get_sessions_overlapping(
            datetime(2026, 6, 1, 10, tzinfo=UTC),
            datetime(2026, 6, 1, 11, tzinfo=UTC),
        )
        self.assertEqual(found, [])

    def test_boundary_touch_is_overlap(self):
        db = _tmp_db()
        db.add_session_window(self._win("101", 8, 10))
        # ended_at == start bound: overlaps (started_at <= end AND ended_at >= start).
        found = db.get_sessions_overlapping(
            datetime(2026, 6, 1, 10, tzinfo=UTC),
            datetime(2026, 6, 1, 12, tzinfo=UTC),
        )
        self.assertEqual(len(found), 1)

    def test_filters_narrow_result(self):
        db = _tmp_db()
        db.add_session_window(self._win("101", 8, 12, repo="a/b"))
        db.add_session_window(self._win("202", 8, 12, repo="c/d"))
        lo = datetime(2026, 6, 1, 9, tzinfo=UTC)
        hi = datetime(2026, 6, 1, 11, tzinfo=UTC)
        self.assertEqual(len(db.get_sessions_overlapping(lo, hi, task_id="101")), 1)
        self.assertEqual(len(db.get_sessions_overlapping(lo, hi, repo="c/d")), 1)
        self.assertEqual(
            len(db.get_sessions_overlapping(lo, hi, strategy_name="development")), 2
        )
        self.assertEqual(
            db.get_sessions_overlapping(lo, hi, strategy_name="nope"), []
        )


class TestMigration(unittest.TestCase):
    def test_legacy_events_table_gets_session_id_column(self):
        import sqlite3

        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        # Simulate a pre-migration DB: events table without session_id.
        conn = sqlite3.connect(tmp.name)
        conn.executescript(
            "CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "source TEXT NOT NULL, timestamp TEXT NOT NULL, task_ids TEXT, "
            "repo TEXT, pr_num INTEGER, branch TEXT, subject TEXT, payload TEXT);"
        )
        conn.execute(
            "INSERT INTO events (source, timestamp, task_ids, repo, pr_num, "
            "branch, subject, payload) VALUES "
            "('commit', '2026-06-01T09:00:00+00:00', '[\"1\"]', 'o/r', 0, '', '', NULL)"
        )
        conn.commit()
        conn.close()
        # Opening through the client migrates and reads the row back.
        db = LocalStateClient(db_path=Path(tmp.name))
        events = db.get_events()
        self.assertEqual(len(events), 1)
        self.assertIsNone(events[0].session_id)
        # Migration is idempotent: a second open does not fail.
        LocalStateClient(db_path=Path(tmp.name))


if __name__ == "__main__":
    unittest.main()
