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
from tests.support import make_state_db

UTC = timezone.utc


def _tmp_db() -> LocalStateClient:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return make_state_db(Path(tmp.name))


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


class TestAgentlessRepoExport(unittest.TestCase):
    """Exports of repo-less (agentless) sessions (#550).

    Every other export test seeds ``repo="acme/web"``, so nothing exercised the
    derivation's absent-repo branch — which is exactly how the ``\\x00``-prefixed
    sentinel reached serialized output unnoticed (#508). These seed ``repo=""``
    and assert the rendered documents stay free of control characters.
    """

    def setUp(self):
        self.db = _tmp_db()
        _commit(self.db, 9, 0, repo="")
        _commit(self.db, 9, 20, repo="")
        _commit(self.db, 10, 30, repo="")

    def test_markdown_export_has_no_control_characters(self):
        text = export_markdown(self.db, date(2026, 6, 1), date(2026, 6, 1))
        self.assertIn("## Final Time Entries", text)
        self.assertNotIn("\x00", text)

    def test_csv_export_has_no_control_characters(self):
        text = export_csv(self.db, date(2026, 6, 1), date(2026, 6, 1))
        lines = text.strip().splitlines()
        self.assertGreater(len(lines), 1)
        self.assertNotIn("\x00", text)

    def test_derived_entries_carry_an_absent_repo(self):
        result, _ = build_result(self.db, date(2026, 6, 1), date(2026, 6, 1))
        self.assertTrue(result.best_gap_entries)
        for entry in result.best_gap_entries:
            self.assertEqual(entry.repo, "")

    def test_mixed_repo_window_still_prefers_the_real_label(self):
        # A repo-less event alongside a labeled one must not drag the entry back
        # to the absent repo — the real label is the display metadata.
        _commit(self.db, 9, 40, repo="acme/web")
        result, _ = build_result(self.db, date(2026, 6, 1), date(2026, 6, 1))
        repos = {entry.repo for entry in result.best_gap_entries}
        self.assertIn("acme/web", repos)
        self.assertNotIn("\x00", "".join(repos))


if __name__ == "__main__":
    unittest.main()
