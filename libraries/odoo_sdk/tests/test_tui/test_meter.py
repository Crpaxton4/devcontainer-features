"""Tests for the pure meter bar."""

import unittest

from odoo_sdk.tui.meter import meter_row


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


if __name__ == "__main__":
    unittest.main()
