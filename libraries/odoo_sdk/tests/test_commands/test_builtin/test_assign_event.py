"""Tests for the AssignEventCommand triage write (#406).

The command is the surface-agnostic write half of triage: it attributes a set of
events to one Odoo task id in a single transaction, so the TUI, MCP, and CLI all
share one validated write path instead of each calling the store directly. These
tests drive the command over a real schema-provisioned store (no live Odoo — the
client is never touched by this command).
"""

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from odoo_sdk.commands.builtin.assign_event import AssignEventCommand
from odoo_sdk.state import EventRecord, LocalStateClient
from tests.support import make_state_db

UTC = timezone.utc


def _tmp_db() -> LocalStateClient:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return make_state_db(Path(tmp.name))


def _unattributed(store: LocalStateClient, external_id: str) -> int:
    record = store.add_event(
        EventRecord(
            id=None,
            source="chatter",
            timestamp=datetime(2026, 6, 1, 9, 0, tzinfo=UTC),
            task_ids=[],
            repo="",
            subject="Standup",
            external_id=external_id,
        )
    )
    return record.id


def _cmd(store: LocalStateClient) -> AssignEventCommand:
    # The command needs no RPC client; a MagicMock stands in and is never called.
    return AssignEventCommand(MagicMock(), state=store)


class TestAssignEventCommand(unittest.TestCase):
    def test_attributes_every_event_in_one_call(self):
        store = _tmp_db()
        ids = [_unattributed(store, f"gcal:m:tick:{n}") for n in range(3)]
        result = _cmd(store).execute(event_ids=ids, task_id=24648)
        self.assertEqual(result["updated"], 3)
        self.assertEqual(result["task_id"], 24648)
        self.assertEqual(result["event_ids"], ids)
        for event_id in ids:
            self.assertEqual(store.get_event(event_id).task_ids, ["24648"])

    def test_never_touches_the_rpc_client(self):
        store = _tmp_db()
        ids = [_unattributed(store, "gcal:solo")]
        client = MagicMock()
        AssignEventCommand(client, state=store).execute(event_ids=ids, task_id=7)
        client.execute.assert_not_called()

    def test_assigned_events_become_derivable(self):
        store = _tmp_db()
        ids = [_unattributed(store, f"gcal:m:tick:{n}") for n in range(2)]
        _cmd(store).execute(event_ids=ids, task_id=24648)
        lo = datetime(2026, 6, 1, tzinfo=UTC)
        hi = datetime(2026, 6, 2, tzinfo=UTC)
        sessions = store.derive_sessions_overlapping(lo, hi, gap_secs=3600)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].task_id, "24648")

    def test_empty_event_list_is_a_noop(self):
        store = _tmp_db()
        result = _cmd(store).execute(event_ids=[], task_id=24648)
        self.assertEqual(result["updated"], 0)

    def test_rejects_non_positive_task_id(self):
        store = _tmp_db()
        ids = [_unattributed(store, "gcal:solo")]
        for bad in (0, -1):
            with self.assertRaises(ValueError):
                _cmd(store).execute(event_ids=ids, task_id=bad)

    def test_rejects_boolean_task_id(self):
        store = _tmp_db()
        ids = [_unattributed(store, "gcal:solo")]
        with self.assertRaises(ValueError):
            _cmd(store).execute(event_ids=ids, task_id=True)

    def test_name_and_description_are_set(self):
        self.assertEqual(AssignEventCommand._name, "assign_event")
        self.assertNotEqual(AssignEventCommand._description, "")


if __name__ == "__main__":
    unittest.main()
