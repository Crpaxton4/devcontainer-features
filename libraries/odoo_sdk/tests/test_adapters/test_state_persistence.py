import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from odoo_sdk.adapters import (
    event_record_to_raw_event,
    load_raw_events,
    persist_session_windows,
    raw_event_to_event_record,
    time_entry_to_session_window,
)
from odoo_sdk.sessionization import (
    EventType,
    RawEvent,
    SessionizationConfig,
    transform,
)
from odoo_sdk.state import EventRecord, LocalStateClient

UTC = timezone.utc


def _tmp_db() -> LocalStateClient:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return LocalStateClient(db_path=Path(tmp.name))


class TestEventConversion(unittest.TestCase):
    def test_record_to_raw_event_maps_source(self):
        rec = EventRecord(
            id=1,
            source="agent",
            timestamp=datetime(2026, 6, 1, 9, tzinfo=UTC),
            task_ids=["1", "2"],
            repo="o/r",
            payload={"pr_title": "t", "pr_body": "b"},
        )
        event = event_record_to_raw_event(rec)
        self.assertEqual(event.event_type, EventType.AGENT)
        self.assertTrue(event.is_release)  # two tasks
        self.assertEqual(event.pr_title, "t")

    def test_unknown_source_defaults_to_commit(self):
        rec = EventRecord(
            id=1,
            source="mystery",
            timestamp=datetime(2026, 6, 1, 9, tzinfo=UTC),
            task_ids=["1"],
            repo="o/r",
        )
        self.assertEqual(
            event_record_to_raw_event(rec).event_type, EventType.COMMIT
        )

    def test_raw_event_to_record_roundtrip(self):
        event = RawEvent(
            timestamp=datetime(2026, 6, 1, 9, tzinfo=UTC),
            task_ids=["101"],
            repo="o/r",
            pr_num=3,
            event_type=EventType.MERGE,
            pr_title="pt",
            pr_body="pb",
        )
        rec = raw_event_to_event_record(event)
        self.assertEqual(rec.source, "merge")
        back = event_record_to_raw_event(rec)
        self.assertEqual(back.event_type, EventType.MERGE)
        self.assertEqual(back.pr_title, "pt")


class TestPersistence(unittest.TestCase):
    def _seed_events(self, db):
        events = [
            RawEvent(
                timestamp=datetime(2026, 6, 1, 9, m, tzinfo=UTC),
                task_ids=["101"],
                repo="o/r",
                pr_num=0,
                event_type=EventType.COMMIT,
            )
            for m in (0, 20, 40)
        ]
        for event in events:
            db.add_event(raw_event_to_event_record(event))

    def test_load_raw_events_reads_back(self):
        db = _tmp_db()
        self._seed_events(db)
        loaded = load_raw_events(db)
        self.assertEqual(len(loaded), 3)
        self.assertTrue(all(isinstance(e, RawEvent) for e in loaded))

    def test_persist_windows_replaces(self):
        db = _tmp_db()
        self._seed_events(db)
        cfg = SessionizationConfig(
            start_date=date(2026, 6, 1), end_date=date(2026, 6, 1)
        )
        events = load_raw_events(db)
        result = transform(events, cfg)
        persist_session_windows(db, result.best_gap_entries)
        first = len(db.get_session_windows())
        self.assertEqual(first, len(result.best_gap_entries))
        # Persisting again with replace should not accumulate duplicates.
        persist_session_windows(db, result.best_gap_entries)
        self.assertEqual(len(db.get_session_windows()), first)

    def test_persist_windows_append(self):
        db = _tmp_db()
        self._seed_events(db)
        cfg = SessionizationConfig(
            start_date=date(2026, 6, 1), end_date=date(2026, 6, 1)
        )
        result = transform(load_raw_events(db), cfg)
        persist_session_windows(db, result.best_gap_entries)
        persist_session_windows(db, result.best_gap_entries, replace=False)
        self.assertEqual(
            len(db.get_session_windows()), 2 * len(result.best_gap_entries)
        )

    def test_time_entry_to_window_fields(self):
        entry = transform(
            [
                RawEvent(
                    timestamp=datetime(2026, 6, 1, 9, tzinfo=UTC),
                    task_ids=["101"],
                    repo="o/r",
                    pr_num=0,
                    event_type=EventType.COMMIT,
                )
            ],
            SessionizationConfig(
                start_date=date(2026, 6, 1), end_date=date(2026, 6, 1)
            ),
        ).best_gap_entries[0]
        window = time_entry_to_session_window(entry)
        self.assertEqual(window.task_id, "101")
        self.assertEqual(window.repo, "o/r")


if __name__ == "__main__":
    unittest.main()
