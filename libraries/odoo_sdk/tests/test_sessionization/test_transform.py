import unittest
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from odoo_sdk.sessionization import (
    EventType,
    SessionizationConfig,
    TimeEntry,
    billable_events,
    build_window_entries,
    sweep,
    target_day_totals,
    transform,
)

from ._helpers import one_day_config, raw_event

UTC = timezone.utc


class TestBillableEvents(unittest.TestCase):
    def test_drops_unknown_and_empty(self):
        events = [raw_event(12, 0, task="101"), raw_event(12, 5, task="")]
        billable = billable_events(events)
        self.assertEqual(len(billable), 1)
        self.assertEqual(billable[0].task_ids, ["101"])


class TestSweep(unittest.TestCase):
    def _events(self):
        return [
            raw_event(9, 0, task="101"),
            raw_event(9, 40, task="101"),
            raw_event(11, 0, task="101"),
            raw_event(11, 20, task="102"),
            raw_event(14, 0, task="102"),
        ]

    def test_best_gap_within_bounds(self):
        cfg = one_day_config()
        result = sweep(self._events(), cfg)
        self.assertGreaterEqual(result.best_gap, cfg.sweep_min_gap_mins)
        self.assertLessEqual(result.best_gap, cfg.sweep_max_gap_mins)

    def test_per_task_matrix_shape(self):
        cfg = one_day_config()
        result = sweep(self._events(), cfg)
        for totals in result.per_task.values():
            self.assertEqual(len(totals), len(result.gap_vals))

    def test_monotonic_non_decreasing_totals(self):
        # Combined billed time is non-decreasing as the gap grows.
        cfg = one_day_config(sweep_min_gap_mins=30, sweep_max_gap_mins=120)
        result = sweep(self._events(), cfg)
        for earlier, later in zip(result.combined, result.combined[1:]):
            self.assertLessEqual(earlier, later + 1e-6)

    def test_empty_events_zero_obs_mean(self):
        result = sweep([], one_day_config())
        self.assertEqual(result.obs_mean, sum(result.combined) / len(result.combined))


class TestTargetDayTotals(unittest.TestCase):
    def test_splits_across_midnight(self):
        cfg = one_day_config(
            start_date=date(2026, 6, 1), end_date=date(2026, 6, 2)
        )
        # A late commit that would billed-round into a window near midnight.
        events = [raw_event(23, 0, task="101"), raw_event(23, 30, task="101")]
        entries = build_window_entries(events, 3600, cfg)
        totals = target_day_totals(entries, cfg)
        self.assertEqual(set(totals), set(cfg.target_dates))

    def test_day_bucket_zone_moves_a_midnight_crossing_session(self):
        # Issue #378 item 11: the same 04:00-04:45 UTC entry buckets to 07-01 in
        # US Central (23:00-23:45 CDT) but to 07-02 in UTC — the configured zone,
        # not a hardcoded offset, decides the day.
        entry = TimeEntry(
            task_id="24648",
            repo="owner/repo",
            pr_num=0,
            start=datetime(2026, 7, 2, 4, 0, tzinfo=UTC),
            end=datetime(2026, 7, 2, 4, 45, tzinfo=UTC),
        )
        base = {"start_date": date(2026, 7, 1), "end_date": date(2026, 7, 2)}
        central = SessionizationConfig(day_bucket_tz=ZoneInfo("America/Chicago"), **base)
        utc = SessionizationConfig(day_bucket_tz=ZoneInfo("UTC"), **base)

        central_totals = target_day_totals([entry], central)
        utc_totals = target_day_totals([entry], utc)

        self.assertEqual(central_totals[date(2026, 7, 1)], 45 * 60.0)
        self.assertEqual(central_totals[date(2026, 7, 2)], 0.0)
        self.assertEqual(utc_totals[date(2026, 7, 1)], 0.0)
        self.assertEqual(utc_totals[date(2026, 7, 2)], 45 * 60.0)


class TestTransform(unittest.TestCase):
    def test_full_pipeline_produces_entries(self):
        cfg = one_day_config()
        events = [
            raw_event(9, 0, task="101"),
            raw_event(9, 30, task="101"),
            raw_event(9, 10, task="101", event_type=EventType.AGENT),
        ]
        result = transform(events, cfg)
        self.assertTrue(result.best_gap_entries)
        self.assertEqual(result.raw_events, events)
        self.assertGreaterEqual(result.sweep.best_gap, cfg.sweep_min_gap_mins)

    def test_unknown_events_kept_in_raw_but_not_billed(self):
        cfg = one_day_config()
        events = [raw_event(9, 0, task="101"), raw_event(9, 5, task="")]
        result = transform(events, cfg)
        self.assertEqual(len(result.raw_events), 2)
        self.assertTrue(
            all(e.task_id != "" for e in result.best_gap_entries)
        )


if __name__ == "__main__":
    unittest.main()
