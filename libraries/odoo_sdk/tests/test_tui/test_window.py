"""Tests for the pure date-window controller."""

import unittest
from datetime import date

from odoo_sdk.tui.window import WINDOW_ACTIONS, DateWindow, apply_action


class TestDateWindowInvariant(unittest.TestCase):
    def test_rejects_start_after_end(self):
        with self.assertRaises(ValueError):
            DateWindow(date(2026, 6, 5), date(2026, 6, 1))

    def test_days_is_inclusive(self):
        self.assertEqual(DateWindow(date(2026, 6, 1), date(2026, 6, 7)).days, 7)

    def test_single_day_window_is_one_day(self):
        self.assertEqual(DateWindow(date(2026, 6, 1), date(2026, 6, 1)).days, 1)


class TestMoves(unittest.TestCase):
    def setUp(self):
        self.window = DateWindow(date(2026, 6, 3), date(2026, 6, 5))

    def test_move_start_earlier_widens(self):
        moved = self.window.move_start_earlier()
        self.assertEqual(moved.start, date(2026, 6, 2))
        self.assertEqual(moved.end, self.window.end)

    def test_move_start_later_narrows(self):
        moved = self.window.move_start_later()
        self.assertEqual(moved.start, date(2026, 6, 4))

    def test_move_start_later_clamped_at_end(self):
        window = DateWindow(date(2026, 6, 5), date(2026, 6, 5))
        moved = window.move_start_later()
        self.assertEqual(moved.start, date(2026, 6, 5))
        self.assertEqual(moved.days, 1)

    def test_move_end_later_widens(self):
        moved = self.window.move_end_later()
        self.assertEqual(moved.end, date(2026, 6, 6))

    def test_move_end_earlier_narrows(self):
        moved = self.window.move_end_earlier()
        self.assertEqual(moved.end, date(2026, 6, 4))

    def test_move_end_earlier_clamped_at_start(self):
        window = DateWindow(date(2026, 6, 5), date(2026, 6, 5))
        moved = window.move_end_earlier()
        self.assertEqual(moved.end, date(2026, 6, 5))

    def test_moves_preserve_invariant(self):
        window = DateWindow(date(2026, 6, 5), date(2026, 6, 5))
        # Repeatedly narrowing must never raise (invariant preserved).
        for _ in range(3):
            window = window.move_start_later().move_end_earlier()
        self.assertLessEqual(window.start, window.end)


class TestIsoHelpers(unittest.TestCase):
    def test_iso_helpers(self):
        window = DateWindow(date(2026, 6, 1), date(2026, 6, 5))
        self.assertEqual(window.start_iso(), "2026-06-01")
        self.assertEqual(window.end_iso(), "2026-06-05")


class TestApplyAction(unittest.TestCase):
    def setUp(self):
        self.window = DateWindow(date(2026, 6, 3), date(2026, 6, 5))

    def test_left_moves_start_earlier(self):
        self.assertEqual(apply_action(self.window, "left").start, date(2026, 6, 2))

    def test_right_moves_start_later(self):
        self.assertEqual(apply_action(self.window, "right").start, date(2026, 6, 4))

    def test_up_moves_end_later(self):
        self.assertEqual(apply_action(self.window, "up").end, date(2026, 6, 6))

    def test_down_moves_end_earlier(self):
        self.assertEqual(apply_action(self.window, "down").end, date(2026, 6, 4))

    def test_unknown_action_is_noop(self):
        self.assertEqual(apply_action(self.window, "zzz"), self.window)

    def test_all_actions_registered(self):
        self.assertEqual(set(WINDOW_ACTIONS), {"left", "right", "up", "down"})


if __name__ == "__main__":
    unittest.main()
