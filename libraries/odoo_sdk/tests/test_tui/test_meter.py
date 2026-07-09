"""Tests for the pure gradient meter."""

import unittest

from odoo_sdk.tui.meter import gradient_cells, lerp_color, meter_row


class TestMeterRow(unittest.TestCase):
    def test_zero_width_is_empty(self):
        self.assertEqual(meter_row(0.5, 0), "")

    def test_half_ratio_fills_half(self):
        self.assertEqual(meter_row(0.5, 10), "█████░░░░░")

    def test_full_ratio_fills_all(self):
        self.assertEqual(meter_row(1.0, 4), "████")

    def test_over_full_ratio_clamped(self):
        self.assertEqual(meter_row(3.0, 4), "████")

    def test_negative_ratio_empty(self):
        self.assertEqual(meter_row(-1.0, 4), "░░░░")


class TestLerpColor(unittest.TestCase):
    def test_endpoints(self):
        low, high = (0, 0, 0), (100, 200, 50)
        self.assertEqual(lerp_color(low, high, 0.0), low)
        self.assertEqual(lerp_color(low, high, 1.0), high)

    def test_midpoint(self):
        self.assertEqual(lerp_color((0, 0, 0), (10, 20, 40), 0.5), (5, 10, 20))

    def test_clamped(self):
        self.assertEqual(lerp_color((0, 0, 0), (10, 10, 10), 5.0), (10, 10, 10))


class TestGradientCells(unittest.TestCase):
    def test_zero_width_empty(self):
        self.assertEqual(gradient_cells(0.5, 0), [])

    def test_cell_count_matches_width(self):
        self.assertEqual(len(gradient_cells(0.5, 8)), 8)

    def test_filled_prefix_matches_ratio(self):
        cells = gradient_cells(0.5, 8)
        filled = [c for c in cells if c[0] == "█"]
        self.assertEqual(len(filled), 4)

    def test_each_cell_carries_rgb_triple(self):
        cells = gradient_cells(1.0, 4)
        for _, rgb in cells:
            self.assertEqual(len(rgb), 3)

    def test_single_cell_does_not_divide_by_zero(self):
        cells = gradient_cells(1.0, 1)
        self.assertEqual(len(cells), 1)


if __name__ == "__main__":
    unittest.main()
