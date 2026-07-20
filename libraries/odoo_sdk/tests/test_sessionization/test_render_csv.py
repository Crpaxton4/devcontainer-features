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

    def test_columns_odoo_populates_itself_are_absent(self):
        # Odoo derives project, company, UoM and the SO line from the task on
        # ``account.analytic.line`` create, so emitting them is a no-op at best
        # and a wrong attribution at worst (#498).
        for dropped in (
            "Project/ID",
            "Company/ID",
            "Unit of Measure/ID",
            "Sales Order Item/ID",
        ):
            self.assertNotIn(dropped, CSV_COLUMNS)

    def test_task_id_header_spelling_is_unchanged(self):
        # ``Task/ID`` vs ``Task/.id`` is unconfirmed against a live import and
        # is deliberately left alone; pin it so it is not changed by accident.
        self.assertIn("Task/ID", CSV_COLUMNS)
        self.assertNotIn("Task/.id", CSV_COLUMNS)

    def test_numeric_task_id_preserved_non_numeric_blank(self):
        result, cfg = _result()
        numeric = entry_to_csv_row(result.best_gap_entries[0], cfg)
        self.assertEqual(numeric["Task/ID"], "101")

        entry = result.best_gap_entries[0]
        entry.task_id = "not-an-id"
        self.assertEqual(entry_to_csv_row(entry, cfg)["Task/ID"], "")

    def test_employee_id_defaults_to_blank_not_a_captured_constant(self):
        result, cfg = _result()
        self.assertIsNone(cfg.odoo_employee_id)
        row = entry_to_csv_row(result.best_gap_entries[0], cfg)
        self.assertEqual(row["Employee/ID"], "")

    def test_employee_id_resolved_at_render_time_wins(self):
        result, cfg = _result()
        row = entry_to_csv_row(result.best_gap_entries[0], cfg, None, 7)
        self.assertEqual(row["Employee/ID"], 7)

    def test_render_stamps_resolved_employee_on_every_row(self):
        result, cfg = _result()
        text = render_odoo_csv(result, cfg, employee_id=7)
        rows = list(csv.DictReader(io.StringIO(text)))
        self.assertTrue(rows)
        self.assertTrue(all(r["Employee/ID"] == "7" for r in rows))

    def test_config_override_used_when_no_render_time_id(self):
        result, cfg = _result()
        cfg.odoo_employee_id = 12
        row = entry_to_csv_row(result.best_gap_entries[0], cfg)
        self.assertEqual(row["Employee/ID"], 12)

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
