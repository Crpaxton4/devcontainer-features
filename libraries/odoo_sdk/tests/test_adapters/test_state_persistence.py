import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from odoo_sdk.adapters import (
    event_record_to_raw_event,
    load_raw_events,
    raw_event_to_event_record,
)
from odoo_sdk.sessionization import EventType, RawEvent
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

    def test_unknown_source_raises(self):
        # Unknown sources must fail loudly rather than silently masquerading as
        # commits, which would corrupt sessionization.
        from odoo_sdk.adapters import UnknownEventSourceError

        rec = EventRecord(
            id=1,
            source="mystery",
            timestamp=datetime(2026, 6, 1, 9, tzinfo=UTC),
            task_ids=["1"],
            repo="o/r",
        )
        with self.assertRaises(UnknownEventSourceError):
            event_record_to_raw_event(rec)

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


class TestLoadRawEvents(unittest.TestCase):
    def test_load_raw_events_reads_back(self):
        db = _tmp_db()
        for m in (0, 20, 40):
            db.add_event(
                raw_event_to_event_record(
                    RawEvent(
                        timestamp=datetime(2026, 6, 1, 9, m, tzinfo=UTC),
                        task_ids=["101"],
                        repo="o/r",
                        pr_num=0,
                        event_type=EventType.COMMIT,
                    )
                )
            )
        loaded = load_raw_events(db)
        self.assertEqual(len(loaded), 3)
        self.assertTrue(all(isinstance(e, RawEvent) for e in loaded))

    def test_load_raw_events_range_bounded(self):
        db = _tmp_db()
        for m in (0, 20, 40):
            db.add_event(
                raw_event_to_event_record(
                    RawEvent(
                        timestamp=datetime(2026, 6, 1, 9, m, tzinfo=UTC),
                        task_ids=["101"],
                        repo="o/r",
                        pr_num=0,
                        event_type=EventType.COMMIT,
                    )
                )
            )
        bounded = load_raw_events(
            db,
            datetime(2026, 6, 1, 9, 10, tzinfo=UTC),
            datetime(2026, 6, 1, 9, 45, tzinfo=UTC),
        )
        self.assertEqual(len(bounded), 2)


if __name__ == "__main__":
    unittest.main()
