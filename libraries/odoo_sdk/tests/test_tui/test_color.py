"""Tests for the pure color quantization LUT."""

import unittest

from odoo_sdk.tui.color import (
    ColorDepth,
    quantize,
    to_ansi16,
    to_ansi256,
)


class TestAnsi256(unittest.TestCase):
    def test_pure_red_maps_to_cube_red(self):
        self.assertEqual(to_ansi256((255, 0, 0)), 196)

    def test_pure_white_maps_to_cube_white(self):
        self.assertEqual(to_ansi256((255, 255, 255)), 231)

    def test_dark_gray_uses_grayscale_ramp(self):
        index = to_ansi256((10, 10, 10))
        self.assertGreaterEqual(index, 232)
        self.assertLessEqual(index, 255)

    def test_out_of_range_channels_are_clamped(self):
        # Negative and >255 channels must not raise and stay in palette range.
        index = to_ansi256((-50, 300, 128))
        self.assertGreaterEqual(index, 16)
        self.assertLessEqual(index, 255)


class TestAnsi16(unittest.TestCase):
    def test_black_maps_to_zero(self):
        self.assertEqual(to_ansi16((0, 0, 0)), 0)

    def test_bright_white_maps_to_fifteen(self):
        self.assertEqual(to_ansi16((255, 255, 255)), 15)

    def test_green_maps_to_a_green_slot(self):
        self.assertIn(to_ansi16((0, 200, 0)), (2, 10))


class TestQuantize(unittest.TestCase):
    def test_truecolor_returns_clamped_triple(self):
        self.assertEqual(quantize((300, -1, 128), ColorDepth.TRUECOLOR), (255, 0, 128))

    def test_ansi256_returns_int(self):
        self.assertIsInstance(quantize((255, 0, 0), ColorDepth.ANSI256), int)

    def test_ansi16_returns_int(self):
        self.assertIsInstance(quantize((255, 255, 255), ColorDepth.ANSI16), int)


if __name__ == "__main__":
    unittest.main()
