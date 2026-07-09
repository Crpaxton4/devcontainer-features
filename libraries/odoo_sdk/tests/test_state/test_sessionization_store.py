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


if __name__ == "__main__":
    unittest.main()
