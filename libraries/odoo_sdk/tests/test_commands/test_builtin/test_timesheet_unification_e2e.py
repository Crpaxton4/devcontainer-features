"""End-to-end regression for the unified timesheet flow (issue #181).

Drives the real FSM tools against a temporary state DB and a fake ``OdooClient``
that records every ``account.analytic.line`` call, so the invariant behind #181
is verified through the whole producer path with no live Odoo:

* (a) exactly ONE ``account.analytic.line`` create per task even when
  ``start_task`` runs twice (idempotent anchor adoption — kills #177).
* (b) command bodies emit NO ``agent`` events themselves — as of #326 the sole
  producer for the MCP tool surface is the ``_event_emitting`` wrapper in
  :mod:`odoo_sdk.mcp.server` (covered by ``tests/test_mcp/test_server_events``),
  so driving the commands directly writes no event rows.
* (d) stop_task writes NO ``account.analytic.line`` row (#325): hours are the
  exclusive job of the TUI/ETL upload path, which reconciles the anchor via the
  unified module; no record deletion is ever attempted.
"""

import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from odoo_sdk.commands.builtin.start_task import StartTaskCommand
from odoo_sdk.commands.builtin.stop_task import StopTaskCommand
from odoo_sdk.commands.builtin.task_note import TaskNoteCommand
from odoo_sdk.state import LocalStateClient, TaskAlreadyRunningError

_START_GUARD = "odoo_sdk.commands.builtin.start_task.assert_odoo_devcontainer"
_STOP_GUARD = "odoo_sdk.commands.builtin.stop_task.assert_odoo_devcontainer"
_NOTE_GUARD = "odoo_sdk.commands.builtin.task_note.assert_odoo_devcontainer"


def _tmp_db() -> LocalStateClient:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return LocalStateClient(db_path=Path(tmp.name))


class _RecordingClient:
    """Fake OdooClient recording every account.analytic.line call.

    It models Odoo's ``account.analytic.line`` faithfully enough to exercise
    anchor adoption: ``create`` returns a scalar id (single-dict semantics) and
    stores the row's ``name``; ``search_read`` honours the ``name = "[/] Work in
    progress"`` filter so only *unreconciled* anchors are adopted; ``write``
    updates the stored name (a reconcile turns a placeholder into a real row so
    it is no longer adoptable). ``message_post`` / employee lookups are answered
    generically.
    """

    def __init__(self) -> None:
        self.uid = 7
        self.calls: list[tuple] = []
        self._rows: dict[int, dict] = {}
        self._next_id = 500

    def execute(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((model, method, args, kwargs))
        if model == "hr.employee":
            return [{"id": 3}]
        if model == "project.task" and method == "message_post":
            return 999
        if model == "account.analytic.line":
            return self._analytic(method, args)
        raise AssertionError(f"unexpected call: {model}.{method}")

    def _analytic(self, method: str, args: tuple) -> Any:
        if method == "search_read":
            domain = args[0]
            wanted = dict((f, v) for f, _, v in domain)
            return [
                {"id": rid}
                for rid, row in sorted(self._rows.items())
                if row["name"] == wanted.get("name")
                and row["task_id"] == wanted.get("task_id")
            ]
        if method == "create":
            vals = args[0]
            new_id = self._next_id
            self._next_id += 1
            self._rows[new_id] = dict(vals)
            return new_id  # scalar, not [id]
        if method == "write":
            ids, vals = args
            flat = ids[0] if ids and isinstance(ids[0], list) else ids
            for rid in flat:
                self._rows[rid].update(vals)
            return True
        raise AssertionError(f"unexpected analytic call: {method}")

    def analytic_calls(self, method: str) -> list[tuple]:
        return [
            c for c in self.calls if c[0] == "account.analytic.line" and c[1] == method
        ]


class TestTimesheetUnificationE2E(unittest.TestCase):
    def _start(self, client, db, **kw):
        with patch(_START_GUARD):
            return StartTaskCommand(client, state=db).execute(**kw)

    def _kwargs(self):
        return {
            "task_id": 24648,
            "task_name": "Fix VAT",
            "project_id": 5,
            "project_name": "Accounting",
        }

    def test_single_create_single_write_and_no_command_body_events(self):
        client = _RecordingClient()
        db = _tmp_db()

        # start_task twice: second call short-circuits on the active session but
        # even if it reached ensure_anchor it would adopt the first row.
        first = self._start(client, db, **self._kwargs())
        self.assertIn("run_id", first)
        # already active; raises before any duplicate anchor is created
        with self.assertRaises(TaskAlreadyRunningError):
            self._start(client, db, **self._kwargs())

        with patch(_NOTE_GUARD), patch(
            "odoo_sdk.commands.builtin.task_note.TaskStateDB", return_value=db
        ):
            TaskNoteCommand(client).execute(24648, "made progress")

        with patch(_STOP_GUARD):
            StopTaskCommand(client, state=db).execute(24648, "finished the VAT fix")

        # (a) exactly one create for the anchor across the whole flow.
        self.assertEqual(len(client.analytic_calls("create")), 1)
        # (d) stop_task writes NO analytic line (#325): the anchor is left for
        # the upload path to reconcile, so this FSM-only flow issues zero writes;
        # no deletion happens (the recording client would crash loudly on unlink).
        self.assertEqual(len(client.analytic_calls("write")), 0)

        # (b) command bodies no longer emit agent events (#326): emission moved
        # to the MCP dispatch wrapper, which this direct-command flow bypasses.
        agent = [e for e in db.get_events() if e.source == "agent"]
        self.assertEqual(agent, [])

    def test_repeated_start_before_reconcile_adopts_open_anchor(self):
        # Two starts on the same task while the first placeholder is still open
        # (unreconciled, 0h) must NOT duplicate the anchor — this is the exact
        # #177 scenario. The active-session guard normally short-circuits the
        # second start; even reaching ensure_anchor directly must adopt.
        client = _RecordingClient()
        db = _tmp_db()
        from datetime import date

        from odoo_sdk.utilities.timesheet import ensure_anchor

        first_id = ensure_anchor(client, 24648, 5, 3, date(2026, 7, 10))
        second_id = ensure_anchor(client, 24648, 5, 3, date(2026, 7, 10))
        self.assertEqual(first_id, second_id)  # adopted the first anchor
        self.assertEqual(len(client.analytic_calls("create")), 1)

    def test_new_anchor_after_reconcile(self):
        # Once a placeholder is reconciled (real description written) it is no
        # longer an open anchor, so a genuinely new work session on the same
        # task creates a fresh anchor rather than reusing the billed row.
        # stop_task no longer reconciles (#325), so the upload path's
        # ``reconcile_session`` (the sole derived-upload hours-writer) is invoked
        # directly here to adopt and close out the open anchor — renaming it off
        # the ``[/] Work in progress`` marker — before the next start.
        from datetime import datetime, timezone

        from odoo_sdk.utilities.timesheet import reconcile_session

        client = _RecordingClient()
        db = _tmp_db()
        self._start(client, db, **self._kwargs())
        with patch(_STOP_GUARD):
            StopTaskCommand(client, state=db).execute(24648, "done")
        reconcile_session(
            client,
            db,
            task_id=24648,
            session_key="24648|1",
            description="[/] done",
            hours=1.5,
            started_at=datetime(2026, 7, 10, 9, 0, tzinfo=timezone.utc),
            ended_at=datetime(2026, 7, 10, 10, 30, tzinfo=timezone.utc),
        )
        self._start(client, db, **self._kwargs())
        self.assertEqual(len(client.analytic_calls("create")), 2)


if __name__ == "__main__":
    unittest.main()
