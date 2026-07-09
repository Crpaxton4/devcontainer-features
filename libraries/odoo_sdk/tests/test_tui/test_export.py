"""Tests for the TUI export helpers (composing the #105 renderers)."""

import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from odoo_sdk.state import EventRecord, LocalStateClient
from odoo_sdk.tui.export import (
    build_result,
    config_for_window,
    export_csv,
    export_markdown,
)

UTC = timezone.utc


def _tmp_db() -> LocalStateClient:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return LocalStateClient(db_path=Path(tmp.name))


def _commit(db, hour, minute, *, day=1, task="101", repo="acme/web"):
    db.add_event(
        EventRecord(
            id=None,
            source="commit",
            timestamp=datetime(2026, 6, day, hour, minute, tzinfo=UTC),
            task_ids=[task],
            repo=repo,
        )
    )


class TestConfigForWindow(unittest.TestCase):
    def test_config_spans_inclusive_window(self):
        config = config_for_window(date(2026, 6, 1), date(2026, 6, 5))
        self.assertEqual(config.start_date, date(2026, 6, 1))
        self.assertEqual(config.end_date, date(2026, 6, 5))

    def test_single_day_window_is_valid(self):
        config = config_for_window(date(2026, 6, 1), date(2026, 6, 1))
        self.assertEqual(config.num_days, 1)


class TestBuildResult(unittest.TestCase):
    def test_result_has_entries_for_recorded_events(self):
        db = _tmp_db()
        _commit(db, 9, 0)
        _commit(db, 9, 20)
        _commit(db, 9, 40)
        result, config = build_result(db, date(2026, 6, 1), date(2026, 6, 1))
        self.assertTrue(result.best_gap_entries)
        self.assertEqual(config.start_date, date(2026, 6, 1))

    def test_empty_db_yields_no_entries(self):
        db = _tmp_db()
        result, _ = build_result(db, date(2026, 6, 1), date(2026, 6, 1))
        self.assertEqual(result.best_gap_entries, [])


class TestRenderers(unittest.TestCase):
    def setUp(self):
        self.db = _tmp_db()
        _commit(self.db, 9, 0)
        _commit(self.db, 9, 20)
        _commit(self.db, 10, 30)

    def test_markdown_export_is_a_document(self):
        text = export_markdown(self.db, date(2026, 6, 1), date(2026, 6, 1))
        self.assertIn("## Final Time Entries", text)
        self.assertIn("```mermaid", text)

    def test_csv_export_has_header_and_rows(self):
        text = export_csv(self.db, date(2026, 6, 1), date(2026, 6, 1))
        lines = text.strip().splitlines()
        self.assertTrue(lines[0].startswith("Date,Description,Project/ID"))
        self.assertGreater(len(lines), 1)

    def test_csv_export_empty_window_is_header_only(self):
        text = export_csv(self.db, date(2026, 7, 1), date(2026, 7, 1))
        self.assertEqual(
            text.strip().splitlines(),
            [
                "Date,Description,Project/ID,"
                "Task/ID,Quantity,Employee/ID,Unit of Measure/ID,"
                "Company/ID,Sales Order Item/ID"
            ],
        )


if __name__ == "__main__":
    unittest.main()
