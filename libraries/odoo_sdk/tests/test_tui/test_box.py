"""Tests for the pure box-panel drawing."""

import unittest

from odoo_sdk.tui.box import ROUNDED, SQUARE, draw_box, place_text


class TestDrawBox(unittest.TestCase):
    def test_dimensions_are_exact(self):
        box = draw_box(10, 4)
        self.assertEqual(len(box), 4)
        self.assertTrue(all(len(row) == 10 for row in box))

    def test_rounded_corners(self):
        box = draw_box(6, 3, chars=ROUNDED)
        self.assertTrue(box[0].startswith("╭"))
        self.assertTrue(box[0].endswith("╮"))
        self.assertTrue(box[-1].startswith("╰"))
        self.assertTrue(box[-1].endswith("╯"))

    def test_square_corners(self):
        box = draw_box(6, 3, chars=SQUARE)
        self.assertTrue(box[0].startswith("┌"))
        self.assertTrue(box[-1].endswith("┘"))

    def test_title_embedded_in_top_border(self):
        box = draw_box(20, 3, "hi")
        self.assertIn(" hi ", box[0])

    def test_title_truncated_when_too_long(self):
        box = draw_box(8, 3, "a-very-long-title")
        self.assertEqual(len(box[0]), 8)

    def test_interior_rows_are_hollow(self):
        box = draw_box(8, 4)
        middle = box[1]
        self.assertTrue(middle.startswith("│"))
        self.assertTrue(middle.endswith("│"))
        self.assertEqual(middle[1:-1], " " * 6)

    def test_rejects_tiny_dimensions(self):
        with self.assertRaises(ValueError):
            draw_box(1, 3)
        with self.assertRaises(ValueError):
            draw_box(3, 1)

    def test_narrow_box_falls_back_to_plain_rule(self):
        # inner_width < 3 -> no title embedding, plain rule of correct width.
        box = draw_box(4, 3, "x")
        self.assertEqual(len(box[0]), 4)


class TestPlaceText(unittest.TestCase):
    def test_overlays_within_bounds(self):
        row = ".........."
        self.assertEqual(place_text(row, "abc", 2), "..abc.....")

    def test_preserves_row_width(self):
        row = "....."
        self.assertEqual(len(place_text(row, "toolongtext", 1)), 5)

    def test_negative_column_clips_left(self):
        row = "....."
        self.assertEqual(place_text(row, "abcde", -2), "cde..")

    def test_column_past_end_is_noop(self):
        row = "....."
        self.assertEqual(place_text(row, "x", 99), row)

    def test_empty_text_is_noop(self):
        row = "....."
        self.assertEqual(place_text(row, "", 1), row)


if __name__ == "__main__":
    unittest.main()
