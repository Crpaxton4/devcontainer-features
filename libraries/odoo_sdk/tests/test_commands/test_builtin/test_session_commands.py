"""Tests for the ingest_sessions and query_sessions builtin commands."""

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from odoo_sdk.commands.builtin import IngestSessionsCommand, QuerySessionsCommand
from odoo_sdk.state import EventRecord, LocalConfig, LocalStateClient

UTC = timezone.utc


def _tmp_state() -> LocalStateClient:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return LocalStateClient(db_path=Path(tmp.name))


def _config(gap_mins: int = 60) -> LocalConfig:
    return LocalConfig(behavior={"session_gap_mins": gap_mins})


def _commit(state, minute, hour=9, day=1, task="101", repo="o/r"):
    return state.add_event(
        EventRecord(
            id=None,
            source="commit",
            timestamp=datetime(2026, 6, day, hour, minute, tzinfo=UTC),
            task_ids=[task],
            repo=repo,
        )
    )


class TestIngestSessionsCommand(unittest.TestCase):
    def _cmd(self, state, config=None):
        return IngestSessionsCommand(
            client=MagicMock(), state=state, config=config or _config()
        )

    def test_ingests_and_links(self):
        state = _tmp_state()
        _commit(state, 0)
        _commit(state, 20)
        _commit(state, 40)
        result = self._cmd(state).execute()
        self.assertEqual(result["events_considered"], 3)
        self.assertEqual(result["gap_mins"], 60)
        self.assertEqual(len(state.get_session_windows()), 1)
        self.assertTrue(all(e.session_id for e in state.get_events()))

    def test_date_range_filters_events(self):
        state = _tmp_state()
        _commit(state, 0, day=1)
        _commit(state, 0, day=5)
        result = self._cmd(state).execute(
            start_date="2026-06-01", end_date="2026-06-01"
        )
        self.assertEqual(result["events_considered"], 1)

    def test_uses_configured_gap(self):
        state = _tmp_state()
        _commit(state, 0, hour=9)
        _commit(state, 0, hour=11)  # 2h apart
        # With a 180-min gap, the two events fall into one session.
        self._cmd(state, config=_config(180)).execute()
        self.assertEqual(len(state.get_session_windows()), 1)

    def test_idempotent(self):
        state = _tmp_state()
        _commit(state, 0)
        _commit(state, 20)
        self._cmd(state).execute()
        self._cmd(state).execute()
        self.assertEqual(len(state.get_session_windows()), 1)

    def test_metadata(self):
        cmd = self._cmd(_tmp_state())
        self.assertEqual(cmd.name, "ingest_sessions")
        self.assertTrue(cmd.description)


class TestQuerySessionsCommand(unittest.TestCase):
    def _ingest(self, state, config=None):
        IngestSessionsCommand(
            client=MagicMock(), state=state, config=config or _config()
        ).execute()

    def _query(self, state):
        return QuerySessionsCommand(client=MagicMock(), state=state, config=_config())

    def test_returns_overlapping_sessions_whole(self):
        state = _tmp_state()
        # A session spanning 09:00-12:00 (built from close commits).
        _commit(state, 0, hour=9)
        _commit(state, 0, hour=10)
        _commit(state, 0, hour=11)
        _commit(state, 0, hour=12)
        self._ingest(state)
        # Query a narrow window inside the session; it returns whole.
        result = self._query(state).execute(
            start_date="2026-06-01", end_date="2026-06-01"
        )
        self.assertEqual(len(result), 1)
        session = result[0]
        self.assertEqual(session["started_at"][11:16], "09:00")
        self.assertEqual(session["ended_at"][11:16], "12:00")
        self.assertEqual(len(session["events"]), 4)

    def test_include_events_toggle(self):
        state = _tmp_state()
        _commit(state, 0)
        _commit(state, 20)
        self._ingest(state)
        result = self._query(state).execute(include_events=False)
        self.assertNotIn("events", result[0])

    def test_filters(self):
        state = _tmp_state()
        _commit(state, 0, task="101", repo="a/b")
        _commit(state, 0, hour=15, task="202", repo="c/d")
        self._ingest(state)
        by_task = self._query(state).execute(task_id="101")
        self.assertEqual(len(by_task), 1)
        self.assertEqual(by_task[0]["task_id"], "101")
        by_repo = self._query(state).execute(repo="c/d")
        self.assertEqual(len(by_repo), 1)

    def test_no_sessions_returns_empty(self):
        self.assertEqual(self._query(_tmp_state()).execute(), [])

    def test_metadata(self):
        cmd = self._query(_tmp_state())
        self.assertEqual(cmd.name, "query_sessions")
        self.assertTrue(cmd.description)


if __name__ == "__main__":
    unittest.main()
