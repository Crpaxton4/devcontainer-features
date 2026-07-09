import unittest
from datetime import date

from odoo_sdk.sessionization import EventType, render_markdown, transform

from ._helpers import one_day_config, raw_event


def _result(**cfg_overrides):
    cfg = one_day_config(**cfg_overrides)
    events = [
        raw_event(9, 0, task="101"),
        raw_event(9, 30, task="101"),
        raw_event(11, 0, task="102", event_type=EventType.MERGE, pr_num=5),
        raw_event(12, 0, task="", event_type=EventType.REVIEW, pr_num=7),
    ]
    return transform(events, cfg), cfg


class TestRenderMarkdown(unittest.TestCase):
    def test_contains_all_sections(self):
        result, cfg = _result()
        md = render_markdown(result, cfg)
        for section in (
            "## Sweep",
            "## Sweep Summary",
            "## Final Time Entries",
            "## Final Window Diagram",
            "## Unresolved Task Sources",
            "```mermaid",
        ):
            self.assertIn(section, md)

    def test_unresolved_sources_listed(self):
        result, cfg = _result()
        md = render_markdown(result, cfg)
        # The REVIEW event with an empty task is UNKNOWN -> listed.
        self.assertIn("excluded from billing output", md)

    def test_no_unknown_sources_message(self):
        cfg = one_day_config()
        events = [raw_event(9, 0, task="101"), raw_event(9, 30, task="101")]
        result = transform(events, cfg)
        md = render_markdown(result, cfg)
        self.assertIn("No UNKNOWN source events were found.", md)

    def test_audit_warning_over_upper_bound(self):
        # Force a very low upper bound so best-gap totals exceed it.
        result, cfg = _result(b_low=0.1, b_high=0.2)
        md = render_markdown(result, cfg)
        self.assertIn("## Audit Warnings", md)
        self.assertIn("over upper bound", md)

    def test_excluded_dates_in_summary(self):
        cfg = one_day_config(
            start_date=date(2026, 6, 1),
            end_date=date(2026, 6, 3),
            target_excluded_dates={date(2026, 6, 2)},
        )
        events = [raw_event(9, 0, task="101"), raw_event(9, 30, task="101")]
        result = transform(events, cfg)
        md = render_markdown(result, cfg)
        self.assertIn("excludes 2026-06-02", md)

    def test_audit_empty_csv_task_id_rows(self):
        cfg = one_day_config()
        # A resolved-but-non-numeric task id survives billing yet yields a
        # blank Odoo Task/ID, triggering the audit warning.
        events = [
            raw_event(9, 0, task="ABC"),
            raw_event(9, 30, task="ABC"),
        ]
        result = transform(events, cfg)
        md = render_markdown(result, cfg)
        self.assertIn("empty CSV Task/ID rows", md)

    def test_ends_with_newline(self):
        result, cfg = _result()
        self.assertTrue(render_markdown(result, cfg).endswith("\n"))


if __name__ == "__main__":
    unittest.main()
