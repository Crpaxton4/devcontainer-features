import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from odoo_sdk.commands.builtin import OptimizeSessionsCommand
from odoo_sdk.commands.builtin.optimize_sessions import _build_config, _parse_date
from odoo_sdk.state import EventRecord, LocalStateClient

UTC = timezone.utc


def _tmp_state() -> LocalStateClient:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return LocalStateClient(db_path=Path(tmp.name))


def _seed(state: LocalStateClient) -> None:
    for minute in (0, 20, 40):
        state.add_event(
            EventRecord(
                id=None,
                source="commit",
                timestamp=datetime(2026, 6, 1, 9, minute, tzinfo=UTC),
                task_ids=["101"],
                repo="owner/repo",
            )
        )


class TestConfigHelpers(unittest.TestCase):
    def test_parse_date(self):
        self.assertEqual(_parse_date("2026-06-01").isoformat(), "2026-06-01")
        self.assertIsNone(_parse_date(None))

    def test_build_config_applies_known_overrides_only(self):
        cfg = _build_config(
            "2026-06-01",
            "2026-06-01",
            {"sweep_step_mins": 10, "bogus": 999, "b_low": None},
        )
        self.assertEqual(cfg.sweep_step_mins, 10)
        self.assertFalse(hasattr(cfg, "bogus"))


class TestOptimizeSessionsCommand(unittest.TestCase):
    def _command(self, state):
        return OptimizeSessionsCommand(client=MagicMock(), state=state)

    def test_returns_summary_without_persisting(self):
        state = _tmp_state()
        _seed(state)
        result = self._command(state).execute(
            start_date="2026-06-01", end_date="2026-06-01"
        )
        self.assertEqual(result["event_count"], 3)
        self.assertGreaterEqual(result["best_gap_mins"], 30)
        self.assertEqual(result["persisted_windows"], 0)
        self.assertEqual(state.get_session_windows(), [])

    def test_persist_writes_windows(self):
        state = _tmp_state()
        _seed(state)
        result = self._command(state).execute(
            start_date="2026-06-01", end_date="2026-06-01", persist=True
        )
        self.assertGreater(result["persisted_windows"], 0)
        self.assertEqual(
            len(state.get_session_windows()), result["persisted_windows"]
        )

    def test_metadata(self):
        cmd = self._command(_tmp_state())
        self.assertEqual(cmd.name, "optimize_sessions")
        self.assertTrue(cmd.description)


if __name__ == "__main__":
    unittest.main()
