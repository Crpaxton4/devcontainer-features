import unittest
from datetime import datetime, timezone

from odoo_sdk.sessionization import EventType, TimeEntry
from odoo_sdk.sessionization.models import utc_now

UTC = timezone.utc


class TestModels(unittest.TestCase):
    def test_time_entry_duration_secs(self):
        entry = TimeEntry(
            task_id="1",
            repo="o/r",
            pr_num=0,
            start=datetime(2026, 6, 1, 9, 0, tzinfo=UTC),
            end=datetime(2026, 6, 1, 9, 30, tzinfo=UTC),
        )
        self.assertEqual(entry.duration_secs, 1800)

    def test_event_type_has_agent(self):
        self.assertIn(EventType.AGENT, set(EventType))

    def test_utc_now_is_tz_aware(self):
        self.assertIsNotNone(utc_now().tzinfo)


if __name__ == "__main__":
    unittest.main()
