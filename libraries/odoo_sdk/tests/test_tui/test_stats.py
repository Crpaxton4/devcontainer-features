"""Tests for the pure session/event statistics utility."""

import unittest

from odoo_sdk.utilities.stats import compute_stats


def _session(sid, task, start, end, secs, *, repo="acme/web", events=None):
    return {
        "session_id": sid,
        "task_id": task,
        "repo": repo,
        "strategy_name": "development",
        "started_at": start,
        "ended_at": end,
        "duration_secs": secs,
        "events": events or [],
    }


def _event(source, ts):
    return {"source": source, "timestamp": ts}


class TestEmpty(unittest.TestCase):
    def test_empty_sessions_zero_everything(self):
        stats = compute_stats([])
        self.assertEqual(stats.session_count, 0)
        self.assertEqual(stats.total_events, 0)
        self.assertEqual(stats.session_hours, 0.0)
        self.assertEqual(stats.peak_concurrency, 0)
        self.assertEqual(stats.overlap_ratio, 0.0)
        self.assertEqual(stats.tasks, [])


class TestCounts(unittest.TestCase):
    def setUp(self):
        self.sessions = [
            _session(
                1,
                "101",
                "2026-06-01T09:00:00",
                "2026-06-01T11:00:00",
                7200,
                events=[
                    _event("commit", "2026-06-01T09:30:00"),
                    _event("merge", "2026-06-01T10:45:00"),
                ],
            ),
            _session(
                2,
                "202",
                "2026-06-02T09:00:00",
                "2026-06-02T10:00:00",
                3600,
                repo="acme/api",
                events=[_event("commit", "2026-06-02T09:15:00")],
            ),
        ]

    def test_session_and_task_counts(self):
        stats = compute_stats(self.sessions)
        self.assertEqual(stats.session_count, 2)
        self.assertEqual(stats.task_count, 2)
        self.assertEqual(stats.tasks, ["101", "202"])

    def test_total_events_and_breakdown(self):
        stats = compute_stats(self.sessions)
        self.assertEqual(stats.total_events, 3)
        self.assertEqual(stats.events_by_type, {"commit": 2, "merge": 1})

    def test_session_hours(self):
        stats = compute_stats(self.sessions)
        self.assertEqual(stats.session_hours, 3.0)

    def test_active_days_and_lanes(self):
        stats = compute_stats(self.sessions)
        self.assertEqual(stats.active_days, 2)
        self.assertEqual(stats.lane_count, 2)

    def test_events_per_day_and_week(self):
        stats = compute_stats(self.sessions)
        self.assertEqual(stats.events_per_day, 1.5)  # 3 events / 2 active days
        self.assertEqual(stats.events_per_week, 10.5)


class TestUtilization(unittest.TestCase):
    def test_target_utilization(self):
        # 8h of session over 1 day against an 8h/day target -> ratio 1.0.
        sessions = [
            _session(1, "1", "2026-06-01T00:00:00", "2026-06-01T08:00:00", 8 * 3600)
        ]
        stats = compute_stats(sessions, target_hours_per_day=8.0)
        self.assertEqual(stats.target_utilization, 1.0)

    def test_calendar_utilization_over_span(self):
        # 2h of session across a 4h calendar span -> 0.5.
        sessions = [
            _session(1, "1", "2026-06-01T09:00:00", "2026-06-01T11:00:00", 7200),
            _session(2, "1", "2026-06-01T12:00:00", "2026-06-01T13:00:00", 3600),
        ]
        stats = compute_stats(sessions)
        self.assertEqual(stats.span_hours, 4.0)
        # session secs = 10800 over span 14400 -> 0.75
        self.assertEqual(stats.calendar_utilization, 0.75)


class TestParallelization(unittest.TestCase):
    def test_no_overlap_ratio_is_one(self):
        # Two disjoint sessions: session-h equals covered wall-clock.
        sessions = [
            _session(1, "1", "2026-06-01T09:00:00", "2026-06-01T10:00:00", 3600),
            _session(2, "2", "2026-06-01T11:00:00", "2026-06-01T12:00:00", 3600),
        ]
        stats = compute_stats(sessions)
        self.assertEqual(stats.overlap_ratio, 1.0)
        self.assertEqual(stats.peak_concurrency, 1)

    def test_full_overlap_doubles_ratio(self):
        # Two sessions fully overlapping: 2h session over 1h covered -> 2.0.
        sessions = [
            _session(1, "1", "2026-06-01T09:00:00", "2026-06-01T10:00:00", 3600),
            _session(2, "2", "2026-06-01T09:00:00", "2026-06-01T10:00:00", 3600),
        ]
        stats = compute_stats(sessions)
        self.assertEqual(stats.overlap_ratio, 2.0)
        self.assertEqual(stats.peak_concurrency, 2)

    def test_touching_sessions_not_double_counted(self):
        # One ends exactly as the next starts -> peak concurrency stays 1.
        sessions = [
            _session(1, "1", "2026-06-01T09:00:00", "2026-06-01T10:00:00", 3600),
            _session(2, "2", "2026-06-01T10:00:00", "2026-06-01T11:00:00", 3600),
        ]
        stats = compute_stats(sessions)
        self.assertEqual(stats.peak_concurrency, 1)

    def test_partial_overlap_peak(self):
        sessions = [
            _session(1, "1", "2026-06-01T09:00:00", "2026-06-01T11:00:00", 7200),
            _session(2, "2", "2026-06-01T10:00:00", "2026-06-01T12:00:00", 7200),
            _session(3, "3", "2026-06-01T10:30:00", "2026-06-01T10:45:00", 900),
        ]
        stats = compute_stats(sessions)
        self.assertEqual(stats.peak_concurrency, 3)


if __name__ == "__main__":
    unittest.main()
