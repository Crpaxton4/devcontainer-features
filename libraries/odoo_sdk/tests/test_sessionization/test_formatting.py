import unittest
from datetime import datetime, timezone

from odoo_sdk.sessionization.formatting import (
    business_context,
    fmt_delta,
    fmt_duration,
    fmt_et,
    is_numeric_id,
    md_table_header,
)

UTC = timezone.utc


class TestFormatting(unittest.TestCase):
    def test_fmt_et(self):
        # Default day-bucket zone is US Central (issue #378 item 11): 16:00 UTC in
        # June is 11:00 CDT, and the label is the resolved zone's abbreviation.
        ts = datetime(2026, 6, 1, 16, 0, tzinfo=UTC)
        self.assertEqual(fmt_et(ts), "2026-06-01 11:00 CDT")

    def test_fmt_duration(self):
        self.assertEqual(fmt_duration(3660), "1h 1m")
        self.assertEqual(fmt_duration(-10), "0h 0m")

    def test_fmt_delta_signs(self):
        self.assertEqual(fmt_delta(0), "0h 0m")
        self.assertEqual(fmt_delta(3660), "+1h 1m")
        self.assertEqual(fmt_delta(-3660), "-1h 1m")

    def test_md_table_header(self):
        header = md_table_header([("A", 3), ("B", 4)])
        self.assertEqual(len(header), 2)
        self.assertTrue(header[0].startswith("| A"))
        self.assertIn("---", header[1])

    def test_is_numeric_id(self):
        self.assertTrue(is_numeric_id("123"))
        self.assertFalse(is_numeric_id(""))
        self.assertFalse(is_numeric_id("abc"))

    def test_business_context_scrubs_identifiers(self):
        text = "Fix PR #42 in owner/repo branch feature https://x.io deadbeef1"
        scrubbed = business_context(text)
        self.assertNotIn("#42", scrubbed)
        self.assertNotIn("owner/repo", scrubbed)
        self.assertNotIn("https://x.io", scrubbed)


if __name__ == "__main__":
    unittest.main()
