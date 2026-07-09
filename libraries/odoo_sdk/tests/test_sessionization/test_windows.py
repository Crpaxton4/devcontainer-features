import unittest
from datetime import datetime, timedelta, timezone

from odoo_sdk.sessionization import ceil_to_billing_step, compute_windows

from ._helpers import one_day_config

UTC = timezone.utc


def _ts(minute: int) -> datetime:
    return datetime(2026, 6, 1, 12, tzinfo=UTC) + timedelta(minutes=minute)


class TestCeilToBillingStep(unittest.TestCase):
    def test_rounds_up_to_step(self):
        cfg = one_day_config(billing_step_mins=15)
        self.assertEqual(ceil_to_billing_step(60, cfg), 900)  # -> 15 min
        self.assertEqual(ceil_to_billing_step(900, cfg), 900)
        self.assertEqual(ceil_to_billing_step(901, cfg), 1800)

    def test_zero_or_negative_passthrough(self):
        cfg = one_day_config()
        self.assertEqual(ceil_to_billing_step(0, cfg), 0)
        self.assertEqual(ceil_to_billing_step(-5, cfg), -5)

    def test_zero_step_passthrough(self):
        cfg = one_day_config(billing_step_mins=0)
        self.assertEqual(ceil_to_billing_step(123, cfg), 123)


class TestComputeWindows(unittest.TestCase):
    def test_empty_returns_empty(self):
        self.assertEqual(compute_windows([], 3600, one_day_config()), [])

    def test_single_timestamp_floored_to_min(self):
        cfg = one_day_config(min_task_minutes=15)
        windows = compute_windows([_ts(0)], 3600, cfg)
        self.assertEqual(len(windows), 1)
        start, end = windows[0]
        self.assertEqual((end - start).total_seconds(), 900)

    def test_split_on_large_gap(self):
        cfg = one_day_config(min_task_minutes=15)
        # 0,20 within gap; 90 separated by > 60 min gap.
        windows = compute_windows([_ts(0), _ts(20), _ts(90)], 3600, cfg)
        self.assertEqual(len(windows), 2)

    def test_merges_within_gap(self):
        cfg = one_day_config(min_task_minutes=15)
        windows = compute_windows([_ts(0), _ts(30), _ts(50)], 3600, cfg)
        self.assertEqual(len(windows), 1)
        start, end = windows[0]
        # 50 min elapsed, rounded up to the next 15-min boundary -> 60 min.
        self.assertEqual((end - start).total_seconds(), 3600)

    def test_sorts_input(self):
        cfg = one_day_config()
        w1 = compute_windows([_ts(50), _ts(0), _ts(30)], 3600, cfg)
        w2 = compute_windows([_ts(0), _ts(30), _ts(50)], 3600, cfg)
        self.assertEqual(w1, w2)


if __name__ == "__main__":
    unittest.main()
