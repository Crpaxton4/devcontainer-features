import unittest
from datetime import datetime, timedelta, timezone

from odoo_sdk.sessionization import billable_seconds, compute_windows

from ._helpers import one_day_config

UTC = timezone.utc


def _ts(minute: int) -> datetime:
    return datetime(2026, 6, 1, 12, tzinfo=UTC) + timedelta(minutes=minute)


class TestBillableSeconds(unittest.TestCase):
    def test_rounds_half_up_to_step(self):
        # Default policy: round to the nearest 0.05h (3 min) step, half-up.
        cfg = one_day_config(round_session_hours=0.05, min_session_hours=0.0)
        self.assertEqual(billable_seconds(3000, cfg), 3060.0)  # 50m -> 51m
        self.assertEqual(billable_seconds(180, cfg), 180.0)  # exact 3m
        self.assertEqual(billable_seconds(270, cfg), 360.0)  # 4.5m -> 6m (up)

    def test_floors_to_minimum(self):
        cfg = one_day_config(min_session_hours=0.25, round_session_hours=0.05)
        # A single-event (zero-span) session floors to the 0.25h minimum.
        self.assertEqual(billable_seconds(0, cfg), 900.0)
        # A short rounded span below the floor is raised to it.
        self.assertEqual(billable_seconds(180, cfg), 900.0)

    def test_zero_step_disables_rounding(self):
        cfg = one_day_config(round_session_hours=0.0, min_session_hours=0.0)
        self.assertEqual(billable_seconds(1234, cfg), 1234.0)


class TestComputeWindows(unittest.TestCase):
    def test_empty_returns_empty(self):
        self.assertEqual(compute_windows([], 3600), [])

    def test_single_timestamp_is_zero_span_window(self):
        windows = compute_windows([_ts(0)], 3600)
        self.assertEqual(len(windows), 1)
        start, end = windows[0]
        self.assertEqual((end - start).total_seconds(), 0)

    def test_split_on_large_gap(self):
        # 0,20 within gap; 90 separated by > 60 min gap.
        windows = compute_windows([_ts(0), _ts(20), _ts(90)], 3600)
        self.assertEqual(len(windows), 2)

    def test_merges_within_gap_raw_bounds(self):
        windows = compute_windows([_ts(0), _ts(30), _ts(50)], 3600)
        self.assertEqual(len(windows), 1)
        start, end = windows[0]
        # Raw bounds: first to last event, no billing rounding applied here.
        self.assertEqual((end - start).total_seconds(), 50 * 60)

    def test_boundary_gap_does_not_split(self):
        # A gap exactly equal to gap_secs is NOT > gap_secs, so no split.
        windows = compute_windows([_ts(0), _ts(60)], 3600)
        self.assertEqual(len(windows), 1)

    def test_sorts_input(self):
        w1 = compute_windows([_ts(50), _ts(0), _ts(30)], 3600)
        w2 = compute_windows([_ts(0), _ts(30), _ts(50)], 3600)
        self.assertEqual(w1, w2)


if __name__ == "__main__":
    unittest.main()
