import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from odoo_dev_core.builtins.optimize_sessions import _build_config, _parse_date
from odoo_dev_data.events import EventRecord, EventStore
from ...support import make_event_store
from ...fakes import service
from odoo_dev_core import default_registry

UTC = timezone.utc


def _tmp_state() -> EventStore:
    return make_event_store()


def _seed(state: EventStore) -> None:
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
        return service(client=MagicMock(), events=state)

    def test_returns_summary(self):
        state = _tmp_state()
        _seed(state)
        result = self._command(state).execute(
            "optimize_sessions", start_date="2026-06-01", end_date="2026-06-01"
        )
        self.assertEqual(result["event_count"], 3)
        self.assertGreaterEqual(result["best_gap_mins"], 30)

    def test_analysis_never_mutates_sessions(self):
        # The optimizer is decoupled/read-only: it must not mutate stored state,
        # and it no longer exposes a persistence knob or count.
        state = _tmp_state()
        _seed(state)
        result = self._command(state).execute(
            "optimize_sessions", start_date="2026-06-01", end_date="2026-06-01"
        )
        self.assertEqual(len(state.get_events()), 3)  # events untouched
        self.assertNotIn("persisted_windows", result)

    def test_metadata(self):
        spec = default_registry().get("optimize_sessions")
        self.assertEqual(spec.id, "optimize_sessions")
        self.assertTrue(spec.description)


if __name__ == "__main__":
    unittest.main()
