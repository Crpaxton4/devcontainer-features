"""Tests for the AbortTaskCommand force-close behaviour."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from odoo_sdk.commands.builtin.abort_task import AbortTaskCommand
from odoo_sdk.state import LocalStateClient as TaskStateDB
from odoo_sdk.state import TaskState

_ABORT_GUARD = "odoo_sdk.commands.builtin.abort_task.assert_odoo_devcontainer"


def _tmp_db() -> TaskStateDB:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return TaskStateDB(db_path=Path(tmp.name))


def _client(uid: int = 7) -> MagicMock:
    c = MagicMock()
    c.uid = uid
    return c


def _cmd_with_db(client, db) -> AbortTaskCommand:
    return AbortTaskCommand(client, state=db)


class TestAbortTaskCommand(unittest.TestCase):
    def test_stops_session_and_unlinks_placeholder_timesheet(self):
        client = _client()
        db = _tmp_db()
        db.create_session(1, "Bug", 10, "Project A", timesheet_id=50)
        with patch(_ABORT_GUARD):
            result = _cmd_with_db(client, db).execute(1)
        # The placeholder timesheet is unlinked (deleted), not written.
        client.execute.assert_called_once_with(
            "account.analytic.line", "unlink", [50]
        )
        self.assertTrue(result["aborted"])
        self.assertEqual(result["timesheet_id"], 50)
        session = db.get_session_by_id(result["session_id"])
        self.assertEqual(session.state, TaskState.STOPPED)

    def test_aborts_from_awaiting_answers(self):
        client = _client()
        db = _tmp_db()
        db.create_session(1, "Bug", 10, "Project A", timesheet_id=50)
        db.transition_to_awaiting(1)
        with patch(_ABORT_GUARD):
            result = _cmd_with_db(client, db).execute(1)
        client.execute.assert_called_once_with(
            "account.analytic.line", "unlink", [50]
        )
        session = db.get_session_by_id(result["session_id"])
        self.assertEqual(session.state, TaskState.STOPPED)

    def test_no_unlink_when_no_placeholder_timesheet(self):
        client = _client()
        db = _tmp_db()
        db.create_session(1, "Bug", 10, "Project A", timesheet_id=None)
        with patch(_ABORT_GUARD):
            result = _cmd_with_db(client, db).execute(1)
        client.execute.assert_not_called()
        self.assertIsNone(result["timesheet_id"])
        session = db.get_session_by_id(result["session_id"])
        self.assertEqual(session.state, TaskState.STOPPED)

    def test_graceful_error_when_no_active_session(self):
        client = _client()
        db = _tmp_db()
        with patch(_ABORT_GUARD):
            result = _cmd_with_db(client, db).execute(999)
        self.assertIn("error", result)
        self.assertIn("No active session", result["error"])
        client.execute.assert_not_called()

    def test_does_not_write_hours(self):
        client = _client()
        db = _tmp_db()
        db.create_session(1, "Bug", 10, "Project A", timesheet_id=50)
        with (
            patch(_ABORT_GUARD),
            patch(
                "odoo_sdk.commands.builtin.stop_task.update_timesheet"
            ) as mock_update,
        ):
            _cmd_with_db(client, db).execute(1)
        # Aborting must never invoke the write path that logs elapsed hours.
        mock_update.assert_not_called()


if __name__ == "__main__":
    unittest.main()
