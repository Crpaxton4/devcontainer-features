"""Tests for AbortRunCommand over the central tracker DB and close_anchor (#331, #369)."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from odoo_sdk.commands.builtin.abort_run import AbortRunCommand
from odoo_sdk.state import (
    LocalStateClient,
    TaskNotRunningError,
    TaskState,
    TrackerStateMissingError,
)
from odoo_sdk.billing.timesheet import ABORTED_ANCHOR_NAME, ANCHOR_NAME, close_anchor
from tests.support import make_state_db

_ABORT_GUARD = "odoo_sdk.commands.builtin.abort_run.assert_odoo_devcontainer"


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


class TestCloseAnchor(unittest.TestCase):
    def test_closes_when_name_is_anchor(self):
        client = _client_with_anchor(ANCHOR_NAME)
        self.assertTrue(close_anchor(client, 50))
        writes = _write_calls(client)
        self.assertEqual(len(writes), 1)
        self.assertEqual(
            writes[0].args[3],
            {"name": ABORTED_ANCHOR_NAME, "unit_amount": 0.0},
        )

    def test_does_not_clobber_human_edited_row(self):
        client = _client_with_anchor("Real work I typed")
        self.assertFalse(close_anchor(client, 50))
        self.assertEqual(_write_calls(client), [])

    def test_returns_false_for_missing_row(self):
        client = _client_with_anchor(None)
        self.assertFalse(close_anchor(client, 50))
        self.assertEqual(_write_calls(client), [])

    def test_returns_false_for_none_timesheet(self):
        client = MagicMock()
        self.assertFalse(close_anchor(client, None))
        client.execute.assert_not_called()


class TestAbortRunCommand(unittest.TestCase):
    def _make_run(self, timesheet_id=50):
        db = make_state_db()
        db.create_run(1, "Wedged", 10, "Proj", timesheet_id=timesheet_id)
        return db, db.get_active_run(1).id

    def _command(self, client, db):
        return AbortRunCommand(client, state=db)

    def test_aborts_active_run_and_closes_anchor(self):
        db, run_id = self._make_run()
        client = _client_with_anchor(ANCHOR_NAME)
        with patch(_ABORT_GUARD):
            result = self._command(client, db).execute(run_id)
        self.assertTrue(result["aborted"])
        self.assertTrue(result["anchor_closed"])
        self.assertFalse(result["already_stopped"])
        self.assertEqual(db.get_run_by_id(run_id).state, TaskState.STOPPED)
        self.assertEqual(len(_write_calls(client)), 1)

    def test_aborts_but_leaves_human_edited_anchor(self):
        db, run_id = self._make_run()
        client = _client_with_anchor("Human notes")
        with patch(_ABORT_GUARD):
            result = self._command(client, db).execute(run_id)
        self.assertTrue(result["aborted"])
        self.assertFalse(result["anchor_closed"])
        self.assertEqual(db.get_run_by_id(run_id).state, TaskState.STOPPED)
        self.assertEqual(_write_calls(client), [])

    def test_resolves_by_task_id(self):
        # A prior stopped run bumps the autoincrement so the active run's SQLite
        # id (2) differs from its task id (5), proving the task-id fallback.
        db = make_state_db()
        db.create_run(9, "Old", 10, "Proj", timesheet_id=1)
        db.stop_run(9)
        db.create_run(5, "Active", 10, "Proj", timesheet_id=50)
        run_id = db.get_active_run(5).id
        self.assertNotEqual(run_id, 5)
        client = _client_with_anchor(ANCHOR_NAME)
        with patch(_ABORT_GUARD):
            result = self._command(client, db).execute(5)
        self.assertTrue(result["aborted"])
        self.assertEqual(result["task_id"], 5)
        self.assertEqual(result["run_id"], run_id)

    def test_already_stopped_run_is_noop(self):
        db, run_id = self._make_run()
        db.stop_run(1)
        client = _client_with_anchor(ANCHOR_NAME)
        with patch(_ABORT_GUARD):
            result = self._command(client, db).execute(run_id)
        self.assertTrue(result["already_stopped"])
        self.assertFalse(result["aborted"])
        client.execute.assert_not_called()

    def test_missing_db_raises_tracker_state_missing(self):
        # The central DB is host-provisioned; abort must not create one (#369).
        missing = Path(tempfile.mkdtemp()) / "absent" / "tracker.db"
        db = LocalStateClient(db_path=missing)
        client = MagicMock()
        with patch(_ABORT_GUARD):
            with self.assertRaises(TrackerStateMissingError):
                self._command(client, db).execute(1)

    def test_missing_run_raises_task_not_running(self):
        db, _ = self._make_run()
        client = MagicMock()
        with patch(_ABORT_GUARD):
            with self.assertRaises(TaskNotRunningError):
                self._command(client, db).execute(9999)


if __name__ == "__main__":
    unittest.main()
