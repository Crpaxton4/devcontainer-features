"""Tests for the pure braille/block graph canvas."""

import unittest

from odoo_sdk.tui.canvas import GraphCanvas, braille_cell, sparkline


class TestSparkline(unittest.TestCase):
    def test_empty_values_yield_empty_string(self):
        self.assertEqual(sparkline([]), "")

    def test_length_matches_input(self):
        self.assertEqual(len(sparkline([1, 2, 3, 4])), 4)

    def test_max_value_is_full_block(self):
        line = sparkline([0, 10])
        self.assertEqual(line[-1], "█")

    def test_zero_ceiling_all_blank(self):
        self.assertEqual(sparkline([0, 0, 0], ceiling=0), " " * 3)

    def test_explicit_ceiling_scales(self):
        # A value of 5 against a ceiling of 10 is a mid block, not full.
        line = sparkline([5], ceiling=10)
        self.assertNotEqual(line, "█")


class TestBrailleCell(unittest.TestCase):
    def test_no_dots_is_blank_braille(self):
        self.assertEqual(braille_cell([]), chr(0x2800))

    def test_top_left_dot_sets_bit_one(self):
        self.assertEqual(braille_cell([(0, 0)]), chr(0x2801))

    def test_out_of_range_dots_ignored(self):
        self.assertEqual(braille_cell([(5, 9), (0, 0)]), chr(0x2801))


class TestGraphCanvas(unittest.TestCase):
    def test_rejects_non_positive_size(self):
        with self.assertRaises(ValueError):
            GraphCanvas(0, 4)
        with self.assertRaises(ValueError):
            GraphCanvas(4, 0)

    def test_push_drops_oldest_when_full(self):
        canvas = GraphCanvas(3, 4)
        for value in (1, 2, 3, 4):
            canvas.push(value)
        self.assertEqual(canvas.samples, [2.0, 3.0, 4.0])

    def test_render_shape_is_height_by_width(self):
        canvas = GraphCanvas(5, 4)
        canvas.push(1)
        rows = canvas.render()
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(len(r) == 5 for r in rows))

    def test_newest_sample_is_rightmost(self):
        canvas = GraphCanvas(4, 3)
        canvas.push(10)  # only sample -> rightmost column filled
        rows = canvas.render()
        bottom = rows[-1]
        self.assertNotEqual(bottom[-1], " ")
        self.assertEqual(bottom[0], " ")

    def test_all_zero_samples_do_not_crash(self):
        canvas = GraphCanvas(3, 2)
        canvas.push(0)
        rows = canvas.render()
        self.assertEqual(len(rows), 2)

    def test_resize_keeps_recent_samples(self):
        canvas = GraphCanvas(5, 3)
        for value in range(5):
            canvas.push(value)
        canvas.resize(3, 2)
        self.assertEqual(canvas.samples, [2.0, 3.0, 4.0])
        self.assertEqual(canvas.width, 3)
        self.assertEqual(canvas.height, 2)
        self.assertEqual(len(canvas.render()), 2)

    def test_resize_rejects_non_positive(self):
        canvas = GraphCanvas(2, 2)
        with self.assertRaises(ValueError):
            canvas.resize(-1, 2)

    def test_ceiling_clips_tall_samples(self):
        canvas = GraphCanvas(2, 2)
        canvas.push(100)
        rows = canvas.render(ceiling=10)  # value far above ceiling -> full column
        self.assertEqual(rows[0][-1], "█")

    def test_negative_ceiling_renders_blank(self):
        # A non-positive explicit ceiling must not raise and yields an empty grid.
        canvas = GraphCanvas(2, 2)
        canvas.push(5)
        rows = canvas.render(ceiling=-3)
        self.assertEqual(rows, [" █", " █"])


if __name__ == "__main__":
    unittest.main()
