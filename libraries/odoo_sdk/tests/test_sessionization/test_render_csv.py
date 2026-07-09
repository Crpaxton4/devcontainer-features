import csv
import io
import unittest

from odoo_sdk.sessionization import (
    CSV_COLUMNS,
    EventType,
    default_description,
    render_odoo_csv,
    transform,
)
from odoo_sdk.sessionization.render_csv import (
    entry_to_csv_row,
    prefixed_description,
    sanitize_description,
)

from ._helpers import one_day_config, raw_event


def _result():
    cfg = one_day_config()
    events = [
        raw_event(9, 0, task="101"),
        raw_event(9, 30, task="101"),
        raw_event(11, 0, task="102", event_type=EventType.MERGE, pr_num=5),
    ]
    return transform(events, cfg), cfg


class TestDescriptions(unittest.TestCase):
    def test_default_description_has_prefix_and_category(self):
        result, _ = _result()
        desc = default_description(result.best_gap_entries[0])
        self.assertTrue(desc.startswith("[/]"))
        self.assertIn(":", desc)

    def test_prefixed_description_uses_action(self):
        result, _ = _result()
        entry = result.best_gap_entries[0]
        desc = prefixed_description(entry, "improved the payment flow")
        self.assertIn("improved the payment flow", desc)

    def test_prefixed_description_falls_back_when_empty(self):
        result, _ = _result()
        entry = result.best_gap_entries[0]
        self.assertEqual(
            prefixed_description(entry, "   "), default_description(entry)
        )

    def test_sanitize_strips_prefix_and_specials(self):
        cleaned = sanitize_description('[/] hello, world\nnext;line')
        self.assertNotIn("[/]", cleaned)
        self.assertNotIn(",", cleaned)
        self.assertNotIn("\n", cleaned)

    def test_unknown_strategy_falls_back_to_first_config(self):
        result, _ = _result()
        entry = result.best_gap_entries[0]
        entry.strategy_name = "does-not-exist"
        # Falls back to the first (development) config without error.
        self.assertTrue(default_description(entry).startswith("[/]"))


class TestRenderCsv(unittest.TestCase):
    def test_header_and_row_count(self):
        result, cfg = _result()
        text = render_odoo_csv(result, cfg)
        rows = list(csv.DictReader(io.StringIO(text)))
        self.assertEqual(
            text.splitlines()[0].split(","), CSV_COLUMNS
        )
        self.assertEqual(len(rows), len(result.best_gap_entries))

    def test_numeric_task_id_preserved_non_numeric_blank(self):
        result, cfg = _result()
        row = entry_to_csv_row(result.best_gap_entries[0], cfg)
        self.assertEqual(row["Employee/ID"], cfg.odoo_employee_id)

    def test_description_override_by_index(self):
        result, cfg = _result()
        text = render_odoo_csv(
            result, cfg, descriptions={0: "reworked the billing engine"}
        )
        rows = list(csv.DictReader(io.StringIO(text)))
        self.assertIn("reworked the billing engine", rows[0]["Description"])

    def test_missing_override_index_falls_back(self):
        result, cfg = _result()
        text = render_odoo_csv(result, cfg, descriptions={})
        rows = list(csv.DictReader(io.StringIO(text)))
        self.assertTrue(all(r["Description"].startswith("[/]") for r in rows))


if __name__ == "__main__":
    unittest.main()
