import unittest
from datetime import date, datetime, timezone

from odoo_sdk.sessionization import ET, SessionizationConfig


class TestConfigValidation(unittest.TestCase):
    def test_rejects_sweep_min_below_two_floors(self):
        with self.assertRaises(ValueError):
            SessionizationConfig(min_task_minutes=15, sweep_min_gap_mins=29)

    def test_accepts_sweep_min_at_boundary(self):
        cfg = SessionizationConfig(min_task_minutes=15, sweep_min_gap_mins=30)
        self.assertEqual(cfg.sweep_min_gap_mins, 30)


class TestDerivedProperties(unittest.TestCase):
    def test_range_start_is_midnight_et(self):
        cfg = SessionizationConfig(start_date=date(2026, 6, 3))
        self.assertEqual(cfg.range_start, datetime(2026, 6, 3, tzinfo=ET))

    def test_range_end_is_day_after_end_date(self):
        cfg = SessionizationConfig(end_date=date(2026, 6, 3))
        self.assertEqual(cfg.range_end, datetime(2026, 6, 4, tzinfo=ET))

    def test_target_dates_span_inclusive(self):
        cfg = SessionizationConfig(
            start_date=date(2026, 6, 1), end_date=date(2026, 6, 3)
        )
        self.assertEqual(
            cfg.target_dates,
            [date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 3)],
        )
        self.assertEqual(cfg.num_days, 3)

    def test_excluded_dates_removed_from_targets(self):
        cfg = SessionizationConfig(
            start_date=date(2026, 6, 1),
            end_date=date(2026, 6, 3),
            target_excluded_dates={date(2026, 6, 2)},
        )
        self.assertNotIn(date(2026, 6, 2), cfg.target_dates)
        self.assertEqual(cfg.num_days, 2)

    def test_min_task_secs(self):
        self.assertEqual(SessionizationConfig(min_task_minutes=15).min_task_secs, 900)

    def test_in_range(self):
        cfg = SessionizationConfig(
            start_date=date(2026, 6, 1), end_date=date(2026, 6, 1)
        )
        self.assertTrue(cfg.in_range(datetime(2026, 6, 1, 12, tzinfo=timezone.utc)))
        self.assertFalse(cfg.in_range(datetime(2026, 5, 31, 12, tzinfo=timezone.utc)))


if __name__ == "__main__":
    unittest.main()
