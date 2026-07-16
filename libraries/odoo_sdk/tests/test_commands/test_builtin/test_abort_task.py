"""Tests for the AbortTaskCommand force-close behaviour (#356)."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from odoo_sdk.commands.builtin.abort_task import AbortTaskCommand
from odoo_sdk.state import LocalStateClient as TaskStateDB
from odoo_sdk.state import TaskNotRunningError, TaskState
from odoo_sdk.utilities.timesheet import ABORTED_ANCHOR_NAME, ANCHOR_NAME
from tests.support import make_state_db

_ABORT_GUARD = "odoo_sdk.commands.builtin.abort_task.assert_odoo_devcontainer"


def _tmp_db() -> TaskStateDB:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return make_state_db(Path(tmp.name))


def _client_with_anchor(name) -> MagicMock:
    """Fake Odoo client: reads return an anchor row named ``name`` (or [])."""
    client = MagicMock()

    def _execute(model, method, *args, **kwargs):
        if method == "read":
            return [] if name is None else [{"id": args[0][0], "name": name}]
        return True

    client.execute.side_effect = _execute
    return client


def _write_calls(client) -> list:
    return [c for c in client.execute.call_args_list if c.args[1] == "write"]


def _cmd_with_db(client, db) -> AbortTaskCommand:
    return AbortTaskCommand(client, state=db)


class TestAbortTaskCommand(unittest.TestCase):
    def test_stops_session_and_closes_anchor(self):
        client = _client_with_anchor(ANCHOR_NAME)
        db = _tmp_db()
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=50)
        with patch(_ABORT_GUARD):
            result = _cmd_with_db(client, db).execute(1)
        # The anchor is retired in place — renamed at 0 hours, never deleted —
        # exactly like the cross-DB abort_run's anchor handling (#331/#356).
        writes = _write_calls(client)
        self.assertEqual(len(writes), 1)
        self.assertEqual(
            writes[0].args[3],
            {"name": ABORTED_ANCHOR_NAME, "unit_amount": 0.0},
        )
        self.assertTrue(result["aborted"])
        self.assertTrue(result["anchor_closed"])
        self.assertEqual(result["timesheet_id"], 50)
        run = db.get_run_by_id(result["run_id"])
        self.assertEqual(run.state, TaskState.STOPPED)
        self.assertEqual(run.timesheet_id, 50)

    def test_stamps_aborted_at_on_the_run(self):
        client = _client_with_anchor(ANCHOR_NAME)
        db = _tmp_db()
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=50)
        with patch(_ABORT_GUARD):
            result = _cmd_with_db(client, db).execute(1)
        # The abort instant is recorded so the upload path can exclude the
        # run's leftover sessions from billing (#356).
        run = db.get_run_by_id(result["run_id"])
        self.assertIsNotNone(run.aborted_at)
        self.assertEqual(run.aborted_at, run.stopped_at)

    def test_does_not_clobber_human_edited_anchor(self):
        client = _client_with_anchor("Real work I typed")
        db = _tmp_db()
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=50)
        with patch(_ABORT_GUARD):
            result = _cmd_with_db(client, db).execute(1)
        self.assertTrue(result["aborted"])
        self.assertFalse(result["anchor_closed"])
        self.assertEqual(_write_calls(client), [])

    def test_aborts_from_awaiting_answers(self):
        client = _client_with_anchor(ANCHOR_NAME)
        db = _tmp_db()
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=50)
        db.transition_to_awaiting(1)
        with patch(_ABORT_GUARD):
            result = _cmd_with_db(client, db).execute(1)
        run = db.get_run_by_id(result["run_id"])
        self.assertEqual(run.state, TaskState.STOPPED)
        self.assertIsNotNone(run.aborted_at)

    def test_aborts_when_no_placeholder_timesheet(self):
        client = _client_with_anchor(None)
        db = _tmp_db()
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=None)
        with patch(_ABORT_GUARD):
            result = _cmd_with_db(client, db).execute(1)
        # No anchor to close: no Odoo call at all is issued for a None id.
        client.execute.assert_not_called()
        self.assertIsNone(result["timesheet_id"])
        self.assertFalse(result["anchor_closed"])
        run = db.get_run_by_id(result["run_id"])
        self.assertEqual(run.state, TaskState.STOPPED)

    def test_unwedge_survives_odoo_failure(self):
        # Aborting is a local escape hatch: an unreachable Odoo must not stop
        # the run from being aborted locally (best-effort anchor close, like
        # start_task's chatter post in #375). Billing is still prevented by
        # the aborted_at stamp.
        client = MagicMock()
        client.execute.side_effect = RuntimeError("odoo down")
        db = _tmp_db()
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=50)
        with patch(_ABORT_GUARD):
            result = _cmd_with_db(client, db).execute(1)
        self.assertTrue(result["aborted"])
        self.assertFalse(result["anchor_closed"])
        run = db.get_run_by_id(result["run_id"])
        self.assertEqual(run.state, TaskState.STOPPED)
        self.assertIsNotNone(run.aborted_at)

    def test_raises_when_no_active_session(self):
        client = _client_with_anchor(ANCHOR_NAME)
        db = _tmp_db()
        with patch(_ABORT_GUARD):
            with self.assertRaises(TaskNotRunningError) as ctx:
                _cmd_with_db(client, db).execute(999)
        self.assertEqual(str(ctx.exception), "No active session for task 999.")
        client.execute.assert_not_called()

    def test_does_not_write_hours(self):
        client = _client_with_anchor(ANCHOR_NAME)
        db = _tmp_db()
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=50)
        with (
            patch(_ABORT_GUARD),
            patch(
                "odoo_sdk.utilities.timesheet.update_timesheet"
            ) as mock_update,
        ):
            _cmd_with_db(client, db).execute(1)
        # Aborting must never invoke the write path that logs elapsed hours;
        # the only permitted write is the 0-hour anchor rename.
        mock_update.assert_not_called()
        writes = _write_calls(client)
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0].args[3]["unit_amount"], 0.0)


if __name__ == "__main__":
    unittest.main()
