"""Tests for the pure timeline lane layout."""

import unittest
from datetime import datetime

from odoo_sdk.tui.timeline import build_timeline


def _session(task, start, end, *, repo="acme/web", strategy="development", events=None):
    return {
        "task_id": task,
        "repo": repo,
        "strategy_name": strategy,
        "started_at": start,
        "ended_at": end,
        "events": events or [],
    }


START = datetime(2026, 6, 1, 0, 0, 0)
END = datetime(2026, 6, 1, 23, 59, 59)


class TestBuildTimeline(unittest.TestCase):
    def test_rejects_zero_width(self):
        with self.assertRaises(ValueError):
            build_timeline([], START, END, 0)

    def test_empty_sessions_no_lanes(self):
        grid = build_timeline([], START, END, 40)
        self.assertEqual(grid.lanes, [])
        self.assertEqual(grid.rows, [])

    def test_one_lane_per_group_key(self):
        sessions = [
            _session("101", "2026-06-01T09:00:00", "2026-06-01T10:00:00"),
            _session("101", "2026-06-01T14:00:00", "2026-06-01T15:00:00"),  # same lane
            _session(
                "202", "2026-06-01T09:00:00", "2026-06-01T10:00:00", repo="acme/api"
            ),
        ]
        grid = build_timeline(sessions, START, END, 48)
        self.assertEqual(len(grid.lanes), 2)

    def test_row_width_matches_requested_width(self):
        sessions = [_session("1", "2026-06-01T09:00:00", "2026-06-01T10:00:00")]
        grid = build_timeline(sessions, START, END, 30)
        self.assertEqual(len(grid.rows[0]), 30)

    def test_bar_is_drawn_within_span(self):
        sessions = [_session("1", "2026-06-01T00:00:00", "2026-06-01T23:59:00")]
        grid = build_timeline(sessions, START, END, 20, show_ticks=False)
        self.assertIn("█", grid.rows[0])

    def test_parallel_sessions_align_across_lanes(self):
        # Two sessions at the same instant in different lanes share filled columns.
        sessions = [
            _session("1", "2026-06-01T09:00:00", "2026-06-01T12:00:00"),
            _session(
                "2", "2026-06-01T09:00:00", "2026-06-01T12:00:00", repo="acme/api"
            ),
        ]
        grid = build_timeline(sessions, START, END, 48, show_ticks=False)
        cols_a = {i for i, c in enumerate(grid.rows[0]) if c == "█"}
        cols_b = {i for i, c in enumerate(grid.rows[1]) if c == "█"}
        self.assertTrue(cols_a & cols_b)

    def test_event_ticks_overlaid(self):
        sessions = [
            _session(
                "1",
                "2026-06-01T09:00:00",
                "2026-06-01T09:00:30",
                events=[{"timestamp": "2026-06-01T18:00:00"}],
            )
        ]
        grid = build_timeline(sessions, START, END, 48, show_ticks=True)
        self.assertIn("╿", grid.rows[0])

    def test_ticks_disabled(self):
        sessions = [
            _session(
                "1",
                "2026-06-01T09:00:00",
                "2026-06-01T09:00:30",
                events=[{"timestamp": "2026-06-01T18:00:00"}],
            )
        ]
        grid = build_timeline(sessions, START, END, 48, show_ticks=False)
        self.assertNotIn("╿", grid.rows[0])

    def test_lane_label_format(self):
        sessions = [_session("101", "2026-06-01T09:00:00", "2026-06-01T10:00:00")]
        grid = build_timeline(sessions, START, END, 40)
        self.assertEqual(grid.lanes[0].label, "#101 web development")

    def test_reversed_bounds_are_normalized(self):
        # A session whose end precedes its start still paints a bar.
        sessions = [_session("1", "2026-06-01T15:00:00", "2026-06-01T09:00:00")]
        grid = build_timeline(sessions, START, END, 40, show_ticks=False)
        self.assertIn("█", grid.rows[0])

    def test_zero_span_does_not_crash(self):
        grid = build_timeline(
            [_session("1", "2026-06-01T09:00:00", "2026-06-01T10:00:00")],
            START,
            START,
            20,
        )
        self.assertEqual(len(grid.rows[0]), 20)

    def test_session_count_recorded(self):
        sessions = [
            _session("1", "2026-06-01T09:00:00", "2026-06-01T10:00:00"),
            _session("1", "2026-06-01T11:00:00", "2026-06-01T12:00:00"),
        ]
        grid = build_timeline(sessions, START, END, 40)
        self.assertEqual(grid.lanes[0].session_count, 2)


if __name__ == "__main__":
    unittest.main()
