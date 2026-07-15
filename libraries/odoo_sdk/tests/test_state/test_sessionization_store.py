import unittest
from datetime import datetime, timezone

from odoo_sdk.state import EventRecord, LocalStateClient
from tests.support import make_state_db

UTC = timezone.utc


def _tmp_db() -> LocalStateClient:
    return make_state_db()


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

    def test_coexists_with_fsm_store(self):
        db = _tmp_db()
        db.add_event(_event(0))
        run = db.create_run(1, "Task 1", 10, "Proj", timesheet_id=5)
        self.assertIsNotNone(run.id)
        self.assertEqual(len(db.get_events()), 1)


def _note_event(minute: int, task="101") -> EventRecord:
    return EventRecord(
        id=None,
        source="agent",
        timestamp=datetime(2026, 6, 1, 9, minute, tzinfo=UTC),
        task_ids=[task],
        repo="",
        subject="task_note",
        payload={"tool": "task_note"},
    )


class TestLastNoteAt(unittest.TestCase):
    def test_returns_none_when_no_note_event(self):
        db = _tmp_db()
        db.add_event(_event(0))  # a commit event, not a task_note
        self.assertIsNone(db.last_note_at(101))

    def test_returns_most_recent_note_timestamp(self):
        db = _tmp_db()
        db.add_event(_note_event(5, task="101"))
        db.add_event(_note_event(30, task="101"))
        self.assertEqual(
            db.last_note_at(101), datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
        )

    def test_ignores_notes_for_other_tasks(self):
        db = _tmp_db()
        db.add_event(_note_event(30, task="999"))
        self.assertIsNone(db.last_note_at(101))

    def test_matches_multi_task_note_event(self):
        db = _tmp_db()
        rec = _note_event(12, task="101")
        rec.task_ids = ["101", "202"]
        db.add_event(rec)
        self.assertEqual(
            db.last_note_at(202), datetime(2026, 6, 1, 9, 12, tzinfo=UTC)
        )

    def test_ignores_non_note_agent_events(self):
        db = _tmp_db()
        rec = _note_event(20, task="101")
        rec.subject = "start_task"
        db.add_event(rec)
        self.assertIsNone(db.last_note_at(101))


if __name__ == "__main__":
    unittest.main()
