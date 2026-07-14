"""Tests for the pure full-screen frame composer."""

import unittest
from datetime import date

from odoo_sdk.tui.frame import compose_frame
from odoo_sdk.tui.window import DateWindow
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


WINDOW = DateWindow(date(2026, 6, 1), date(2026, 6, 2))
SESSIONS = [
    _session(
        1,
        "101",
        "2026-06-01T09:00:00",
        "2026-06-01T11:00:00",
        7200,
        events=[{"source": "commit", "timestamp": "2026-06-01T09:30:00"}],
    ),
    _session(
        2, "202", "2026-06-01T10:00:00", "2026-06-01T12:30:00", 9000, repo="acme/api"
    ),
]


class TestCompose(unittest.TestCase):
    def test_frame_is_exactly_width_by_height(self):
        frame = compose_frame(SESSIONS, WINDOW, 100, 24)
        self.assertEqual(len(frame.rows), 24)
        self.assertTrue(all(len(row) == 100 for row in frame.rows))

    def test_header_shows_window_and_counts(self):
        frame = compose_frame(SESSIONS, WINDOW, 100, 24)
        header = frame.rows[0]
        self.assertIn("2026-06-01", header)
        self.assertIn("2026-06-02", header)
        self.assertIn("2 sessions", header)

    def test_footer_shows_keys(self):
        frame = compose_frame(SESSIONS, WINDOW, 100, 24)
        self.assertIn("export", frame.rows[-1])
        self.assertIn("quit", frame.rows[-1])
        self.assertIn("r:resync", frame.rows[-1])

    def test_timeline_panel_titled(self):
        frame = compose_frame(SESSIONS, WINDOW, 100, 24)
        body = "\n".join(frame.rows)
        self.assertIn("timeline", body)
        self.assertIn("stats", body)

    def test_empty_sessions_render_placeholder(self):
        frame = compose_frame([], WINDOW, 100, 24)
        body = "\n".join(frame.rows)
        self.assertIn("no sessions", body)

    def test_empty_hint_rendered_with_guidance(self):
        # Issue #332: the diagnostic hint replaces the bare placeholder and a
        # static guidance line names a next step.
        hint = "no sessions derivable — 7 events in window, 2 runs recorded, gap=30m"
        frame = compose_frame([], WINDOW, 160, 24, empty_hint=hint)
        body = "\n".join(frame.rows)
        self.assertIn("7 events in window", body)
        self.assertIn("widen the window", body)

    def test_empty_hint_distinguishes_no_data_from_not_derivable(self):
        no_data = compose_frame(
            [], WINDOW, 100, 24, empty_hint="no sessions derivable — 0 events in window"
        )
        has_data = compose_frame(
            [], WINDOW, 100, 24, empty_hint="no sessions derivable — 9 events in window"
        )
        self.assertIn("0 events in window", "\n".join(no_data.rows))
        self.assertIn("9 events in window", "\n".join(has_data.rows))

    def test_empty_hint_truncated_at_narrow_width(self):
        # A long hint must not overflow the panel; every row stays exact width.
        hint = "no sessions derivable — 123 events in window, 45 runs recorded, gap=30m"
        frame = compose_frame([], WINDOW, 40, 8, empty_hint=hint)
        self.assertEqual(len(frame.rows), 8)
        self.assertTrue(all(len(row) == 40 for row in frame.rows))

    def test_empty_hint_ignored_when_sessions_present(self):
        # A stale hint never leaks onto a populated window.
        frame = compose_frame(SESSIONS, WINDOW, 100, 24, empty_hint="should not show")
        self.assertNotIn("should not show", "\n".join(frame.rows))

    def test_reflow_to_smaller_size(self):
        # KEY_RESIZE path: recomposing at a new size stays exact and does not crash.
        small = compose_frame(SESSIONS, WINDOW, 60, 12)
        self.assertEqual(len(small.rows), 12)
        self.assertTrue(all(len(row) == 60 for row in small.rows))

    def test_reflow_to_larger_size(self):
        large = compose_frame(SESSIONS, WINDOW, 160, 40)
        self.assertEqual(len(large.rows), 40)
        self.assertTrue(all(len(row) == 160 for row in large.rows))

    def test_too_small_terminal_shows_message(self):
        # A roomy-but-below-threshold terminal still shows the whole message.
        frame = compose_frame(SESSIONS, WINDOW, 39, 7)
        self.assertEqual(len(frame.rows), 7)
        self.assertIn("too small", frame.rows[0])

    def test_narrow_but_valid_terminal_renders_both_panels(self):
        # Just above the two-panel minimum must still draw without crashing.
        frame = compose_frame(SESSIONS, WINDOW, 40, 8)
        self.assertEqual(len(frame.rows), 8)
        self.assertTrue(all(len(row) == 40 for row in frame.rows))

    def test_tiny_terminal_message_clipped_to_width(self):
        frame = compose_frame(SESSIONS, WINDOW, 10, 3)
        self.assertEqual(len(frame.rows), 3)
        self.assertTrue(all(len(row) == 10 for row in frame.rows))

    def test_precomputed_stats_reused(self):
        stats = compute_stats(SESSIONS)
        frame = compose_frame(SESSIONS, WINDOW, 100, 24, stats=stats)
        self.assertIn("2 sessions", frame.rows[0])

    def test_offset_aware_sessions_render_without_crash(self):
        # Issue #333: sessions with +00:00 offsets are subtracted from the
        # date-window bounds while rendering; this must not raise TypeError and
        # must draw the session bar in the timeline panel.
        aware = [
            _session(
                1,
                "101",
                "2026-06-01T09:00:00+00:00",
                "2026-06-01T11:00:00+00:00",
                7200,
            )
        ]
        frame = compose_frame(aware, WINDOW, 100, 24)
        body = "\n".join(frame.rows)
        self.assertIn("█", body)
        self.assertEqual(len(frame.rows), 24)

    def test_naive_sessions_still_render(self):
        # Naive stored timestamps (no offset) still render through the guard.
        frame = compose_frame(SESSIONS, WINDOW, 100, 24)
        self.assertIn("█", "\n".join(frame.rows))

    def test_many_lanes_do_not_overflow_panel(self):
        many = [
            _session(
                i,
                str(i),
                "2026-06-01T09:00:00",
                "2026-06-01T10:00:00",
                3600,
                repo=f"acme/r{i}",
            )
            for i in range(50)
        ]
        frame = compose_frame(many, WINDOW, 100, 16)
        self.assertEqual(len(frame.rows), 16)
        self.assertTrue(all(len(row) == 100 for row in frame.rows))


if __name__ == "__main__":
    unittest.main()
