"""Tests for the 7 task-tracking Command subclasses."""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from odoo_sdk.commands.builtin.resume_task import ResumeTaskCommand
from odoo_sdk.commands.builtin.start_task import StartTaskCommand
from odoo_sdk.commands.builtin.stop_task import StopTaskCommand
from odoo_sdk.commands.builtin.task_list import TaskListCommand
from odoo_sdk.commands.builtin.task_note import TaskNoteCommand
from odoo_sdk.commands.builtin.task_question import TaskQuestionCommand
from odoo_sdk.commands.builtin.task_status import TaskStatusCommand
from odoo_sdk.task_tracker.state import TaskNotRunningError, TaskStateDB

_LIST_GUARD = "odoo_sdk.commands.builtin.task_list.assert_odoo_devcontainer"
_STATUS_GUARD = "odoo_sdk.commands.builtin.task_status.assert_odoo_devcontainer"
_NOTE_GUARD = "odoo_sdk.commands.builtin.task_note.assert_odoo_devcontainer"
_QUESTION_GUARD = "odoo_sdk.commands.builtin.task_question.assert_odoo_devcontainer"
_RESUME_GUARD = "odoo_sdk.commands.builtin.resume_task.assert_odoo_devcontainer"
_START_GUARD = "odoo_sdk.commands.builtin.start_task.assert_odoo_devcontainer"
_STOP_GUARD = "odoo_sdk.commands.builtin.stop_task.assert_odoo_devcontainer"


def _tmp_db() -> TaskStateDB:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return TaskStateDB(db_path=Path(tmp.name))


def _client(uid: int = 7) -> MagicMock:
    c = MagicMock()
    c.uid = uid
    return c


def _accepted(data) -> MagicMock:
    r = MagicMock()
    r.action = "accept"
    r.data = data
    return r


def _cancelled() -> MagicMock:
    r = MagicMock()
    r.action = "cancel"
    return r


# ── TaskListCommand ───────────────────────────────────────────────────────────

class TestTaskListCommand(unittest.TestCase):
    def test_searches_without_filters(self):
        client = _client()
        client.execute.return_value = [{"id": 1, "name": "Bug fix"}]
        with patch(_LIST_GUARD):
            result = TaskListCommand(client).execute()
        client.execute.assert_called_once()
        call = client.execute.call_args
        domain = call.args[2][0]
        self.assertIn(("user_ids", "in", [7]), domain)
        self.assertEqual(result, [{"id": 1, "name": "Bug fix"}])

    def test_applies_stage_filter(self):
        client = _client()
        client.execute.return_value = []
        with patch(_LIST_GUARD):
            TaskListCommand(client).execute(stage="Done")
        call = client.execute.call_args
        domain = call.args[2][0]
        self.assertIn(("stage_id.name", "ilike", "Done"), domain)

    def test_returns_empty_when_no_project_match(self):
        client = _client()
        with (
            patch(_LIST_GUARD),
            patch(
                "odoo_sdk.commands.builtin.task_list.name_search_projects",
                return_value=[],
            ),
        ):
            result = TaskListCommand(client).execute(project_name_query="xyz")
        self.assertEqual(result, [])

    def test_adds_project_id_filter_when_found(self):
        client = _client()
        client.execute.return_value = []
        with (
            patch(_LIST_GUARD),
            patch(
                "odoo_sdk.commands.builtin.task_list.name_search_projects",
                return_value=[{"id": 5, "name": "Acct"}],
            ),
        ):
            TaskListCommand(client).execute(project_name_query="Acct")
        call = client.execute.call_args
        domain = call.args[2][0]
        self.assertIn(("project_id", "in", [5]), domain)

    def test_respects_limit(self):
        client = _client()
        client.execute.return_value = []
        with patch(_LIST_GUARD):
            TaskListCommand(client).execute(limit=5)
        call = client.execute.call_args
        self.assertEqual(call.args[3]["limit"], 5)


# ── TaskStatusCommand ─────────────────────────────────────────────────────────

class TestTaskStatusCommand(unittest.TestCase):
    def test_returns_empty_list_when_no_sessions(self):
        db = _tmp_db()
        with (
            patch(_STATUS_GUARD),
            patch("odoo_sdk.commands.builtin.task_status.TaskStateDB", return_value=db),
        ):
            result = TaskStatusCommand(_client()).execute()
        self.assertEqual(result, [])

    def test_returns_active_sessions(self):
        db = _tmp_db()
        db.create_session(1, "Bug", 10, "Project A", timesheet_id=1)
        db.create_session(2, "Feature", 10, "Project A", timesheet_id=2)
        with (
            patch(_STATUS_GUARD),
            patch("odoo_sdk.commands.builtin.task_status.TaskStateDB", return_value=db),
        ):
            result = TaskStatusCommand(_client()).execute()
        self.assertEqual(len(result), 2)
        task_ids = {r["task_id"] for r in result}
        self.assertEqual(task_ids, {1, 2})

    def test_result_contains_required_keys(self):
        db = _tmp_db()
        db.create_session(1, "Bug", 10, "Project A", timesheet_id=1)
        with (
            patch(_STATUS_GUARD),
            patch("odoo_sdk.commands.builtin.task_status.TaskStateDB", return_value=db),
        ):
            result = TaskStatusCommand(_client()).execute()
        self.assertIn("elapsed", result[0])
        self.assertIn("state", result[0])
        self.assertIn("started_at", result[0])


# ── TaskNoteCommand ───────────────────────────────────────────────────────────

class TestTaskNoteCommand(unittest.TestCase):
    def test_posts_note_and_appends_to_session(self):
        client = _client()
        db = _tmp_db()
        db.create_session(1, "Bug", 10, "Project A", timesheet_id=1)
        with (
            patch(_NOTE_GUARD),
            patch("odoo_sdk.commands.builtin.task_note.TaskStateDB", return_value=db),
            patch(
                "odoo_sdk.commands.builtin.task_note.post_chatter_note",
                return_value=55,
            ) as mock_post,
        ):
            result = TaskNoteCommand(client).execute(1, "Note text")
        mock_post.assert_called_once_with(client, 1, "Note text")
        self.assertEqual(result["message_id"], 55)
        session = db.get_active_session(1)
        self.assertIn("Note text", session.notes)  # type: ignore[union-attr]

    def test_raises_when_no_active_session(self):
        db = _tmp_db()
        with (
            patch(_NOTE_GUARD),
            patch("odoo_sdk.commands.builtin.task_note.TaskStateDB", return_value=db),
        ):
            with self.assertRaises(TaskNotRunningError):
                TaskNoteCommand(_client()).execute(999, "note")


# ── TaskQuestionCommand ───────────────────────────────────────────────────────

class TestTaskQuestionCommand(unittest.TestCase):
    def test_posts_prefixed_question_and_transitions(self):
        client = _client()
        db = _tmp_db()
        db.create_session(1, "Bug", 10, "Project A", timesheet_id=1)
        with (
            patch(_QUESTION_GUARD),
            patch("odoo_sdk.commands.builtin.task_question.TaskStateDB", return_value=db),
            patch(
                "odoo_sdk.commands.builtin.task_question.post_chatter_note",
                return_value=77,
            ) as mock_post,
        ):
            result = TaskQuestionCommand(client).execute(1, "Which approach?")
        mock_post.assert_called_once_with(client, 1, "[?] Which approach?")
        self.assertEqual(result["state"], "AWAITING_ANSWERS")
        self.assertEqual(result["message_id"], 77)

    def test_self_loop_on_awaiting_answers(self):
        client = _client()
        db = _tmp_db()
        db.create_session(1, "Bug", 10, "Project A", timesheet_id=1)
        db.transition_to_awaiting(1)
        with (
            patch(_QUESTION_GUARD),
            patch("odoo_sdk.commands.builtin.task_question.TaskStateDB", return_value=db),
            patch("odoo_sdk.commands.builtin.task_question.post_chatter_note", return_value=78),
        ):
            result = TaskQuestionCommand(client).execute(1, "Another question?")
        self.assertEqual(result["state"], "AWAITING_ANSWERS")

    def test_raises_when_no_active_session(self):
        db = _tmp_db()
        with (
            patch(_QUESTION_GUARD),
            patch("odoo_sdk.commands.builtin.task_question.TaskStateDB", return_value=db),
        ):
            with self.assertRaises(TaskNotRunningError):
                TaskQuestionCommand(_client()).execute(999, "?")


# ── ResumeTaskCommand ─────────────────────────────────────────────────────────

class TestResumeTaskCommand(unittest.TestCase):
    def test_transitions_and_posts_note(self):
        client = _client()
        db = _tmp_db()
        db.create_session(1, "Bug", 10, "Project A", timesheet_id=1)
        db.transition_to_awaiting(1)
        with (
            patch(_RESUME_GUARD),
            patch("odoo_sdk.commands.builtin.resume_task.TaskStateDB", return_value=db),
            patch(
                "odoo_sdk.commands.builtin.resume_task.post_chatter_note",
                return_value=88,
            ) as mock_post,
        ):
            result = ResumeTaskCommand(client).execute(1)
        mock_post.assert_called_once()
        chatter_body = mock_post.call_args.args[2]
        self.assertIn("Resuming", chatter_body)
        self.assertEqual(result["state"], "RUNNING")
        self.assertIn("resumed_at", result)

    def test_raises_when_running(self):
        db = _tmp_db()
        db.create_session(1, "Bug", 10, "Project A", timesheet_id=1)
        from odoo_sdk.task_tracker.state import InvalidStateTransitionError
        with (
            patch(_RESUME_GUARD),
            patch("odoo_sdk.commands.builtin.resume_task.TaskStateDB", return_value=db),
        ):
            with self.assertRaises(InvalidStateTransitionError):
                ResumeTaskCommand(_client()).execute(1)


# ── StartTaskCommand ──────────────────────────────────────────────────────────

class TestStartTaskCommand(unittest.TestCase):
    def _run(self, coro):
        return asyncio.run(coro)

    def _ctx(self, *responses) -> MagicMock:
        ctx = MagicMock()
        ctx.elicit = AsyncMock(side_effect=list(responses))
        return ctx

    def test_single_project_and_task_with_confirmation(self):
        client = _client()
        db = _tmp_db()
        ctx = self._ctx(_accepted(MagicMock(confirmed=True)))
        with (
            patch(_START_GUARD),
            patch("odoo_sdk.commands.builtin.start_task.TaskStateDB", return_value=db),
            patch(
                "odoo_sdk.commands.builtin.start_task.name_search_projects",
                return_value=[{"id": 5, "name": "Accounting"}],
            ),
            patch(
                "odoo_sdk.commands.builtin.start_task.name_search_tasks",
                return_value=[{"id": 10, "name": "Fix VAT"}],
            ),
            patch("odoo_sdk.commands.builtin.start_task.get_employee_id", return_value=3),
            patch("odoo_sdk.commands.builtin.start_task.create_timesheet", return_value=99),
            patch("odoo_sdk.commands.builtin.start_task.post_chatter_note"),
        ):
            result = self._run(StartTaskCommand(client).execute("VAT", ctx, "Accounting"))
        self.assertEqual(result["task_id"], 10)
        self.assertEqual(result["task_name"], "Fix VAT")
        self.assertEqual(result["timesheet_id"], 99)

    def test_cancels_on_declined_confirmation(self):
        client = _client()
        db = _tmp_db()
        ctx = self._ctx(_cancelled())
        with (
            patch(_START_GUARD),
            patch("odoo_sdk.commands.builtin.start_task.TaskStateDB", return_value=db),
            patch(
                "odoo_sdk.commands.builtin.start_task.name_search_projects",
                return_value=[{"id": 5, "name": "Accounting"}],
            ),
            patch(
                "odoo_sdk.commands.builtin.start_task.name_search_tasks",
                return_value=[{"id": 10, "name": "Fix VAT"}],
            ),
        ):
            result = self._run(StartTaskCommand(client).execute("VAT", ctx))
        self.assertIn("error", result)
        self.assertIn("cancel", result["error"].lower())

    def test_disambiguates_multiple_projects(self):
        client = _client()
        db = _tmp_db()
        ctx = self._ctx(
            _accepted(MagicMock(selection=2)),
            _accepted(MagicMock(confirmed=True)),
        )
        with (
            patch(_START_GUARD),
            patch("odoo_sdk.commands.builtin.start_task.TaskStateDB", return_value=db),
            patch(
                "odoo_sdk.commands.builtin.start_task.name_search_projects",
                return_value=[{"id": 1, "name": "HR"}, {"id": 2, "name": "Accounting"}],
            ),
            patch(
                "odoo_sdk.commands.builtin.start_task.name_search_tasks",
                return_value=[{"id": 10, "name": "Fix VAT"}],
            ),
            patch("odoo_sdk.commands.builtin.start_task.get_employee_id", return_value=3),
            patch("odoo_sdk.commands.builtin.start_task.create_timesheet", return_value=99),
            patch("odoo_sdk.commands.builtin.start_task.post_chatter_note"),
        ):
            result = self._run(StartTaskCommand(client).execute("VAT", ctx))
        self.assertEqual(result["task_id"], 10)

    def test_disambiguates_multiple_tasks(self):
        client = _client()
        db = _tmp_db()
        ctx = self._ctx(
            _accepted(MagicMock(selection=1)),
            _accepted(MagicMock(confirmed=True)),
        )
        with (
            patch(_START_GUARD),
            patch("odoo_sdk.commands.builtin.start_task.TaskStateDB", return_value=db),
            patch(
                "odoo_sdk.commands.builtin.start_task.name_search_projects",
                return_value=[{"id": 5, "name": "Accounting"}],
            ),
            patch(
                "odoo_sdk.commands.builtin.start_task.name_search_tasks",
                return_value=[{"id": 10, "name": "Fix VAT"}, {"id": 11, "name": "Fix Rounding"}],
            ),
            patch("odoo_sdk.commands.builtin.start_task.get_employee_id", return_value=3),
            patch("odoo_sdk.commands.builtin.start_task.create_timesheet", return_value=99),
            patch("odoo_sdk.commands.builtin.start_task.post_chatter_note"),
        ):
            result = self._run(StartTaskCommand(client).execute("Fix", ctx))
        self.assertEqual(result["task_id"], 10)

    def test_error_when_no_projects(self):
        client = _client()
        ctx = MagicMock()
        with (
            patch(_START_GUARD),
            patch(
                "odoo_sdk.commands.builtin.start_task.name_search_projects",
                return_value=[],
            ),
            patch("odoo_sdk.commands.builtin.start_task.TaskStateDB", return_value=_tmp_db()),
        ):
            result = self._run(StartTaskCommand(client).execute("x", ctx))
        self.assertIn("error", result)

    def test_error_when_no_tasks(self):
        client = _client()
        ctx = MagicMock()
        with (
            patch(_START_GUARD),
            patch(
                "odoo_sdk.commands.builtin.start_task.name_search_projects",
                return_value=[{"id": 5, "name": "Acct"}],
            ),
            patch(
                "odoo_sdk.commands.builtin.start_task.name_search_tasks",
                return_value=[],
            ),
            patch("odoo_sdk.commands.builtin.start_task.TaskStateDB", return_value=_tmp_db()),
        ):
            result = self._run(StartTaskCommand(client).execute("x", ctx))
        self.assertIn("error", result)

    def test_error_when_already_active(self):
        client = _client()
        db = _tmp_db()
        db.create_session(10, "Fix VAT", 5, "Accounting", timesheet_id=1)
        ctx = self._ctx(_accepted(MagicMock(confirmed=True)))
        with (
            patch(_START_GUARD),
            patch("odoo_sdk.commands.builtin.start_task.TaskStateDB", return_value=db),
            patch(
                "odoo_sdk.commands.builtin.start_task.name_search_projects",
                return_value=[{"id": 5, "name": "Accounting"}],
            ),
            patch(
                "odoo_sdk.commands.builtin.start_task.name_search_tasks",
                return_value=[{"id": 10, "name": "Fix VAT"}],
            ),
        ):
            result = self._run(StartTaskCommand(client).execute("VAT", ctx))
        self.assertIn("error", result)

    def test_uses_cached_employee_id(self):
        client = _client()
        db = _tmp_db()
        db.set_setting("employee_id", "42")
        ctx = self._ctx(_accepted(MagicMock(confirmed=True)))
        with (
            patch(_START_GUARD),
            patch("odoo_sdk.commands.builtin.start_task.TaskStateDB", return_value=db),
            patch(
                "odoo_sdk.commands.builtin.start_task.name_search_projects",
                return_value=[{"id": 5, "name": "Acct"}],
            ),
            patch(
                "odoo_sdk.commands.builtin.start_task.name_search_tasks",
                return_value=[{"id": 10, "name": "Task"}],
            ),
            patch("odoo_sdk.commands.builtin.start_task.get_employee_id") as mock_eid,
            patch("odoo_sdk.commands.builtin.start_task.create_timesheet", return_value=1),
            patch("odoo_sdk.commands.builtin.start_task.post_chatter_note"),
        ):
            self._run(StartTaskCommand(client).execute("Task", ctx))
        mock_eid.assert_not_called()

    def test_out_of_range_project_selection_errors(self):
        client = _client()
        db = _tmp_db()
        ctx = self._ctx(_accepted(MagicMock(selection=99)))
        with (
            patch(_START_GUARD),
            patch("odoo_sdk.commands.builtin.start_task.TaskStateDB", return_value=db),
            patch(
                "odoo_sdk.commands.builtin.start_task.name_search_projects",
                return_value=[{"id": 1, "name": "HR"}, {"id": 2, "name": "IT"}],
            ),
        ):
            result = self._run(StartTaskCommand(client).execute("x", ctx))
        self.assertIn("error", result)

    def test_cancelled_project_selection_errors(self):
        client = _client()
        db = _tmp_db()
        ctx = self._ctx(_cancelled())
        with (
            patch(_START_GUARD),
            patch("odoo_sdk.commands.builtin.start_task.TaskStateDB", return_value=db),
            patch(
                "odoo_sdk.commands.builtin.start_task.name_search_projects",
                return_value=[{"id": 1, "name": "HR"}, {"id": 2, "name": "IT"}],
            ),
        ):
            result = self._run(StartTaskCommand(client).execute("x", ctx))
        self.assertIn("error", result)

    def test_cancelled_task_selection_errors(self):
        client = _client()
        db = _tmp_db()
        ctx = self._ctx(_cancelled())
        with (
            patch(_START_GUARD),
            patch("odoo_sdk.commands.builtin.start_task.TaskStateDB", return_value=db),
            patch(
                "odoo_sdk.commands.builtin.start_task.name_search_projects",
                return_value=[{"id": 5, "name": "Acct"}],
            ),
            patch(
                "odoo_sdk.commands.builtin.start_task.name_search_tasks",
                return_value=[{"id": 10, "name": "A"}, {"id": 11, "name": "B"}],
            ),
        ):
            result = self._run(StartTaskCommand(client).execute("x", ctx))
        self.assertIn("error", result)

    def test_out_of_range_task_selection_errors(self):
        client = _client()
        db = _tmp_db()
        ctx = self._ctx(_accepted(MagicMock(selection=99)))
        with (
            patch(_START_GUARD),
            patch("odoo_sdk.commands.builtin.start_task.TaskStateDB", return_value=db),
            patch(
                "odoo_sdk.commands.builtin.start_task.name_search_projects",
                return_value=[{"id": 5, "name": "Acct"}],
            ),
            patch(
                "odoo_sdk.commands.builtin.start_task.name_search_tasks",
                return_value=[{"id": 10, "name": "A"}, {"id": 11, "name": "B"}],
            ),
        ):
            result = self._run(StartTaskCommand(client).execute("x", ctx))
        self.assertIn("error", result)


# ── StopTaskCommand ───────────────────────────────────────────────────────────

class TestStopTaskCommand(unittest.TestCase):
    def _run(self, coro):
        return asyncio.run(coro)

    def _ctx_accept(self, description: str) -> MagicMock:
        ctx = MagicMock()
        ctx.elicit = AsyncMock(return_value=_accepted(MagicMock(description=description)))
        return ctx

    def _ctx_cancel(self) -> MagicMock:
        ctx = MagicMock()
        ctx.elicit = AsyncMock(return_value=_cancelled())
        return ctx

    def test_stops_session_and_updates_timesheet(self):
        client = _client()
        db = _tmp_db()
        db.create_session(1, "Bug", 10, "Project A", timesheet_id=50)
        ctx = self._ctx_accept("Fixed the bug")
        with (
            patch(_STOP_GUARD),
            patch("odoo_sdk.commands.builtin.stop_task.TaskStateDB", return_value=db),
            patch("odoo_sdk.commands.builtin.stop_task.update_timesheet") as mock_update,
        ):
            result = self._run(StopTaskCommand(client).execute(1, "Fixed the bug", ctx))
        mock_update.assert_called_once()
        self.assertIn("elapsed", result)
        self.assertIn("[/]", result["description"])
        session = db.get_session_by_id(result["session_id"])
        from odoo_sdk.task_tracker.state import TaskState
        self.assertEqual(session.state, TaskState.STOPPED)

    def test_description_not_double_prefixed(self):
        client = _client()
        db = _tmp_db()
        db.create_session(1, "Bug", 10, "Project A", timesheet_id=50)
        ctx = self._ctx_accept("[/] Already prefixed")
        with (
            patch(_STOP_GUARD),
            patch("odoo_sdk.commands.builtin.stop_task.TaskStateDB", return_value=db),
            patch("odoo_sdk.commands.builtin.stop_task.update_timesheet"),
        ):
            result = self._run(StopTaskCommand(client).execute(1, "", ctx))
        self.assertTrue(result["description"].startswith("[/]"))
        self.assertFalse(result["description"].startswith("[/] [/]"))

    def test_cancels_when_elicitation_declined(self):
        client = _client()
        db = _tmp_db()
        db.create_session(1, "Bug", 10, "Project A", timesheet_id=50)
        ctx = self._ctx_cancel()
        with (
            patch(_STOP_GUARD),
            patch("odoo_sdk.commands.builtin.stop_task.TaskStateDB", return_value=db),
        ):
            result = self._run(StopTaskCommand(client).execute(1, "desc", ctx))
        self.assertIn("error", result)

    def test_raises_when_no_active_session(self):
        db = _tmp_db()
        ctx = self._ctx_accept("done")
        with (
            patch(_STOP_GUARD),
            patch("odoo_sdk.commands.builtin.stop_task.TaskStateDB", return_value=db),
        ):
            with self.assertRaises(TaskNotRunningError):
                self._run(StopTaskCommand(_client()).execute(999, "desc", ctx))

    def test_stop_from_awaiting_answers(self):
        client = _client()
        db = _tmp_db()
        db.create_session(1, "Bug", 10, "Project A", timesheet_id=50)
        db.transition_to_awaiting(1)
        ctx = self._ctx_accept("Answers received, no changes needed")
        with (
            patch(_STOP_GUARD),
            patch("odoo_sdk.commands.builtin.stop_task.TaskStateDB", return_value=db),
            patch("odoo_sdk.commands.builtin.stop_task.update_timesheet"),
        ):
            result = self._run(StopTaskCommand(client).execute(1, "done", ctx))
        from odoo_sdk.task_tracker.state import TaskState
        session = db.get_session_by_id(result["session_id"])
        self.assertEqual(session.state, TaskState.STOPPED)

    def test_skips_timesheet_update_when_no_timesheet_id(self):
        client = _client()
        db = _tmp_db()
        db.create_session(1, "Bug", 10, "Project A", timesheet_id=None)
        ctx = self._ctx_accept("done")
        with (
            patch(_STOP_GUARD),
            patch("odoo_sdk.commands.builtin.stop_task.TaskStateDB", return_value=db),
            patch("odoo_sdk.commands.builtin.stop_task.update_timesheet") as mock_update,
        ):
            self._run(StopTaskCommand(client).execute(1, "done", ctx))
        mock_update.assert_not_called()


if __name__ == "__main__":
    unittest.main()
