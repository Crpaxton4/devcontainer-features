import unittest
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from odoo_dev_core.sessionization import SessionizationConfig

ET = ZoneInfo("America/Chicago")


class _Settings:
    """A settings view carrying only the four knobs ``from_settings`` reads."""

    session_gap_secs = 1800
    min_session_hours = 0.2
    round_session_hours = 0.1
    day_bucket_tz = ZoneInfo("UTC")


class TestConfigValidation(unittest.TestCase):
    def test_rejects_sweep_min_below_two_floors(self):
        # 2 * 0.25h == 30 min, so a 29-min sweep floor is rejected.
        with self.assertRaises(ValueError):
            SessionizationConfig(min_session_hours=0.25, sweep_min_gap_mins=29)

    def test_accepts_sweep_min_at_boundary(self):
        cfg = SessionizationConfig(min_session_hours=0.25, sweep_min_gap_mins=30)
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

    def test_billing_policy_defaults_match_upload(self):
        cfg = SessionizationConfig()
        self.assertEqual(cfg.min_session_hours, 0.25)
        self.assertEqual(cfg.round_session_hours, 0.05)
        self.assertEqual(cfg.session_gap_secs, 3600)

    def test_from_settings_reads_the_four_knobs(self):
        cfg = SessionizationConfig.from_settings(
            _Settings(), start_date=date(2026, 6, 1), end_date=date(2026, 6, 1)
        )
        self.assertEqual(cfg.session_gap_secs, 1800)
        self.assertEqual(cfg.min_session_hours, 0.2)
        self.assertEqual(cfg.round_session_hours, 0.1)
        self.assertEqual(cfg.day_bucket_tz, ZoneInfo("UTC"))
        self.assertEqual(cfg.start_date, date(2026, 6, 1))

    def test_from_settings_moves_the_range_bounds(self):
        cfg = SessionizationConfig.from_settings(
            _Settings(), start_date=date(2026, 6, 3), end_date=date(2026, 6, 3)
        )
        self.assertEqual(cfg.range_start, datetime(2026, 6, 3, tzinfo=ZoneInfo("UTC")))

    def test_in_range(self):
        cfg = SessionizationConfig(
            start_date=date(2026, 6, 1), end_date=date(2026, 6, 1)
        )
        self.assertTrue(cfg.in_range(datetime(2026, 6, 1, 12, tzinfo=timezone.utc)))
        self.assertFalse(cfg.in_range(datetime(2026, 5, 31, 12, tzinfo=timezone.utc)))


if __name__ == "__main__":
    unittest.main()
