"""Tests for the pure Markdown/HTML conversion helpers."""

import unittest

from odoo_sdk.utilities.html import html_to_markdown, markdown_to_html


class TestMarkdownToHtml(unittest.TestCase):
    """``markdown_to_html`` renders Markdown to an HTML fragment."""

    def test_empty_string_returns_empty(self):
        self.assertEqual(markdown_to_html(""), "")

    def test_whitespace_only_returns_empty(self):
        self.assertEqual(markdown_to_html("   \n\t  "), "")

    def test_heading_renders_h_tag(self):
        self.assertIn("<h1>Title</h1>", markdown_to_html("# Title"))

    def test_bold_renders_strong(self):
        self.assertIn("<strong>bold</strong>", markdown_to_html("**bold**"))

    def test_bullet_list_renders_ul_and_li(self):
        html = markdown_to_html("- a\n- b")
        self.assertIn("<ul>", html)
        self.assertIn("<li>a</li>", html)
        self.assertIn("<li>b</li>", html)

    def test_paragraph_break_produces_separate_paragraphs(self):
        html = markdown_to_html("first\n\nsecond")
        self.assertIn("<p>first</p>", html)
        self.assertIn("<p>second</p>", html)

    def test_table_renders_table_markup(self):
        md = "| a | b |\n| - | - |\n| 1 | 2 |"
        html = markdown_to_html(md)
        self.assertIn("<table>", html)
        self.assertIn("<td>1</td>", html)

    def test_output_has_no_surrounding_whitespace(self):
        html = markdown_to_html("hello")
        self.assertEqual(html, html.strip())


class TestRoundTrip(unittest.TestCase):
    """``html_to_markdown`` reverses ``markdown_to_html`` structurally."""

    def test_heading_and_bullets_survive_round_trip(self):
        md = "# Plan\n\n- first\n- second"
        recovered = html_to_markdown(markdown_to_html(md))
        self.assertIn("# Plan", recovered)
        self.assertIn("first", recovered)
        self.assertIn("second", recovered)
        # Bullet structure is preserved rather than collapsed to one line.
        self.assertIn("* first", recovered)
        self.assertIn("* second", recovered)


if __name__ == "__main__":
    unittest.main()
