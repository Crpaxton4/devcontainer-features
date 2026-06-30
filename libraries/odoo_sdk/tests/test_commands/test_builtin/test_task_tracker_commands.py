"""Tests for task-tracking Command subclasses."""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from odoo_sdk.commands.builtin.get_task import GetTaskCommand
from odoo_sdk.commands.builtin.get_task_chatter import GetTaskChatterCommand
from odoo_sdk.commands.builtin.resume_task import ResumeTaskCommand
from odoo_sdk.commands.builtin.start_task import StartTaskCommand
from odoo_sdk.commands.builtin.stop_task import StopTaskCommand
from odoo_sdk.commands.builtin.task_list import TaskListCommand
from odoo_sdk.commands.builtin.task_note import TaskNoteCommand
from odoo_sdk.commands.builtin.task_question import TaskQuestionCommand
from odoo_sdk.commands.builtin.task_status import TaskStatusCommand
from odoo_sdk.task_tracker.state import TaskNotRunningError, TaskStateDB

_SP_PATCH = "odoo_sdk.commands.builtin.start_task.subprocess"

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


def _make_sp(current_branch: str = "main", branches: tuple = ("main",), dirty: bool = False) -> MagicMock:
    """Build a subprocess mock for start_task git branch helpers."""
    sp = MagicMock()
    def _run(args, **kwargs):
        r = MagicMock()
        r.returncode = 0
        r.stdout = ""
        if "rev-parse" in args:
            r.stdout = f"{current_branch}\n"
        elif args[1] == "branch":
            r.stdout = "".join(f"{b}\n" for b in branches)
        elif args[1] == "status":
            r.stdout = "M file.py\n" if dirty else ""
        return r
    sp.run.side_effect = _run
    return sp


# ── GetTaskChatterCommand ─────────────────────────────────────────────────────

class TestGetTaskChatterCommand(unittest.TestCase):
    def test_delegates_to_odoo_ops(self):
        client = _client()
        expected = [{"id": 1, "author": "Jane", "body": "Hello"}]
        with patch(
            "odoo_sdk.commands.builtin.get_task_chatter.get_task_chatter",
            return_value=expected,
        ) as mock_chatter:
            result = GetTaskChatterCommand(client).execute(task_id=42)
        mock_chatter.assert_called_once_with(client, 42, limit=100)
        self.assertEqual(result, expected)

    def test_passes_custom_limit(self):
        client = _client()
        with patch(
            "odoo_sdk.commands.builtin.get_task_chatter.get_task_chatter",
            return_value=[],
        ) as mock_chatter:
            GetTaskChatterCommand(client).execute(task_id=10, limit=5)
        mock_chatter.assert_called_once_with(client, 10, limit=5)


# ── GetTaskCommand ────────────────────────────────────────────────────────────

class TestGetTaskCommand(unittest.TestCase):
    def test_returns_none_when_task_not_found(self):
        client = _client()
        with (
            patch("odoo_sdk.commands.builtin.get_task.get_task_detail", return_value=None),
            patch("odoo_sdk.commands.builtin.get_task.get_task_chatter") as mock_chatter,
        ):
            result = GetTaskCommand(client).execute(task_id=999)
        self.assertIsNone(result)
        mock_chatter.assert_not_called()

    def test_merges_chatter_into_task(self):
        client = _client()
        task_data = {"task_id": 42, "name": "Feature X", "description": "Do it"}
        chatter_data = [{"id": 1, "author": "Jane", "body": "Note"}]
        with (
            patch("odoo_sdk.commands.builtin.get_task.get_task_detail", return_value=task_data),
            patch("odoo_sdk.commands.builtin.get_task.get_task_chatter", return_value=chatter_data),
        ):
            result = GetTaskCommand(client).execute(task_id=42)
        self.assertEqual(result["chatter"], chatter_data)
        self.assertEqual(result["name"], "Feature X")

    def test_calls_both_helpers_with_same_task_id(self):
        client = _client()
        with (
            patch(
                "odoo_sdk.commands.builtin.get_task.get_task_detail",
                return_value={"task_id": 7, "name": "T"},
            ) as mock_detail,
            patch(
                "odoo_sdk.commands.builtin.get_task.get_task_chatter",
                return_value=[],
            ) as mock_chatter,
        ):
            GetTaskCommand(client).execute(task_id=7)
        mock_detail.assert_called_once_with(client, 7)
        mock_chatter.assert_called_once_with(client, 7)


# ── TaskListCommand ───────────────────────────────────────────────────────────

class TestTaskListCommand(unittest.TestCase):
    def test_searches_without_filters(self):
        client = _client()
        client.execute.return_value = [{"id": 1, "name": "Bug fix"}]
        with patch(_LIST_GUARD):
            result = TaskListCommand(client).execute()
        client.execute.assert_called_once()
        call = client.execute.call_args
        domain = call.args[2]
        self.assertIn(("user_ids", "in", [7]), domain)
        self.assertEqual(result, [{"id": 1, "name": "Bug fix"}])

    def test_applies_stage_filter(self):
        client = _client()
        client.execute.return_value = []
        with patch(_LIST_GUARD):
            TaskListCommand(client).execute(stage="Done")
        call = client.execute.call_args
        domain = call.args[2]
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
        domain = call.args[2]
        self.assertIn(("project_id", "in", [5]), domain)

    def test_respects_limit(self):
        client = _client()
        client.execute.return_value = []
        with patch(_LIST_GUARD):
            TaskListCommand(client).execute(limit=5)
        call = client.execute.call_args
        self.assertEqual(call.kwargs["limit"], 5)


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
        ctx = self._ctx(_accepted(MagicMock(confirmed=True)), _accepted(MagicMock(selection=1)))
        ctx.sample = AsyncMock(return_value=MagicMock(text="fix-vat"))
        with (
            patch(_START_GUARD),
            patch(_SP_PATCH, _make_sp()),
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
            _accepted(MagicMock(selection=1)),
        )
        ctx.sample = AsyncMock(return_value=MagicMock(text="fix-vat"))
        with (
            patch(_START_GUARD),
            patch(_SP_PATCH, _make_sp()),
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
            _accepted(MagicMock(selection=1)),
        )
        ctx.sample = AsyncMock(return_value=MagicMock(text="fix-vat"))
        with (
            patch(_START_GUARD),
            patch(_SP_PATCH, _make_sp()),
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
        ctx = self._ctx(_accepted(MagicMock(confirmed=True)), _accepted(MagicMock(selection=1)))
        ctx.sample = AsyncMock(return_value=MagicMock(text="fix-vat"))
        with (
            patch(_START_GUARD),
            patch(_SP_PATCH, _make_sp()),
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
        ctx = self._ctx(_accepted(MagicMock(confirmed=True)), _accepted(MagicMock(selection=1)))
        ctx.sample = AsyncMock(return_value=MagicMock(text="task"))
        with (
            patch(_START_GUARD),
            patch(_SP_PATCH, _make_sp()),
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

    def test_task_id_bypasses_name_search(self):
        client = _client()
        db = _tmp_db()
        client.execute.return_value = [{"id": 10, "name": "Fix VAT", "project_id": [5, "Accounting"]}]
        ctx = self._ctx(_accepted(MagicMock(confirmed=True)), _accepted(MagicMock(selection=1)))
        ctx.sample = AsyncMock(return_value=MagicMock(text="fix-vat"))
        with (
            patch(_START_GUARD),
            patch(_SP_PATCH, _make_sp()),
            patch("odoo_sdk.commands.builtin.start_task.TaskStateDB", return_value=db),
            patch("odoo_sdk.commands.builtin.start_task.name_search_projects") as mock_proj,
            patch("odoo_sdk.commands.builtin.start_task.name_search_tasks") as mock_task,
            patch("odoo_sdk.commands.builtin.start_task.get_employee_id", return_value=3),
            patch("odoo_sdk.commands.builtin.start_task.create_timesheet", return_value=99),
            patch("odoo_sdk.commands.builtin.start_task.post_chatter_note"),
        ):
            result = self._run(StartTaskCommand(client).execute("Fix VAT", ctx, task_id=10))
        mock_proj.assert_not_called()
        mock_task.assert_not_called()
        self.assertEqual(result["task_id"], 10)
        self.assertEqual(result["task_name"], "Fix VAT")
        self.assertEqual(result["project_name"], "Accounting")

    def test_task_id_lookup_extracts_project_from_tuple(self):
        client = _client()
        db = _tmp_db()
        client.execute.return_value = [{"id": 20, "name": "Task", "project_id": [7, "HR"]}]
        ctx = self._ctx(_accepted(MagicMock(confirmed=True)), _accepted(MagicMock(selection=1)))
        ctx.sample = AsyncMock(return_value=MagicMock(text="task"))
        with (
            patch(_START_GUARD),
            patch(_SP_PATCH, _make_sp()),
            patch("odoo_sdk.commands.builtin.start_task.TaskStateDB", return_value=db),
            patch("odoo_sdk.commands.builtin.start_task.name_search_projects"),
            patch("odoo_sdk.commands.builtin.start_task.name_search_tasks"),
            patch("odoo_sdk.commands.builtin.start_task.get_employee_id", return_value=3),
            patch("odoo_sdk.commands.builtin.start_task.create_timesheet", return_value=1),
            patch("odoo_sdk.commands.builtin.start_task.post_chatter_note"),
        ):
            result = self._run(StartTaskCommand(client).execute("Task", ctx, task_id=20))
        self.assertEqual(result["project_name"], "HR")

    def test_task_id_fallback_to_name_search_with_warning(self):
        client = _client()
        db = _tmp_db()
        client.execute.return_value = []  # task_id lookup fails
        ctx = self._ctx(_accepted(MagicMock(confirmed=True)), _accepted(MagicMock(selection=1)))
        ctx.sample = AsyncMock(return_value=MagicMock(text="fix-vat"))
        with (
            patch(_START_GUARD),
            patch(_SP_PATCH, _make_sp()),
            patch("odoo_sdk.commands.builtin.start_task.TaskStateDB", return_value=db),
            patch(
                "odoo_sdk.commands.builtin.start_task.name_search_projects",
                return_value=[{"id": 5, "name": "Acct"}],
            ),
            patch(
                "odoo_sdk.commands.builtin.start_task.name_search_tasks",
                return_value=[{"id": 10, "name": "Fix VAT"}],
            ),
            patch("odoo_sdk.commands.builtin.start_task.get_employee_id", return_value=3),
            patch("odoo_sdk.commands.builtin.start_task.create_timesheet", return_value=99),
            patch("odoo_sdk.commands.builtin.start_task.post_chatter_note"),
        ):
            result = self._run(StartTaskCommand(client).execute("Fix VAT", ctx, task_id=99))
        self.assertIn("warning", result)
        self.assertEqual(result["task_id"], 10)

    def test_task_id_not_found_and_no_name_query_returns_error(self):
        client = _client()
        client.execute.return_value = []
        ctx = MagicMock()
        with (
            patch(_START_GUARD),
            patch("odoo_sdk.commands.builtin.start_task.TaskStateDB", return_value=_tmp_db()),
        ):
            result = self._run(StartTaskCommand(client).execute("", ctx, task_id=999))
        self.assertIn("error", result)
        self.assertIn("999", result["error"])

    def test_task_id_result_has_no_warning_on_success(self):
        client = _client()
        db = _tmp_db()
        client.execute.return_value = [{"id": 10, "name": "Fix VAT", "project_id": [5, "Acct"]}]
        ctx = self._ctx(_accepted(MagicMock(confirmed=True)), _accepted(MagicMock(selection=1)))
        ctx.sample = AsyncMock(return_value=MagicMock(text="fix-vat"))
        with (
            patch(_START_GUARD),
            patch(_SP_PATCH, _make_sp()),
            patch("odoo_sdk.commands.builtin.start_task.TaskStateDB", return_value=db),
            patch("odoo_sdk.commands.builtin.start_task.name_search_projects"),
            patch("odoo_sdk.commands.builtin.start_task.name_search_tasks"),
            patch("odoo_sdk.commands.builtin.start_task.get_employee_id", return_value=3),
            patch("odoo_sdk.commands.builtin.start_task.create_timesheet", return_value=1),
            patch("odoo_sdk.commands.builtin.start_task.post_chatter_note"),
        ):
            result = self._run(StartTaskCommand(client).execute("Fix VAT", ctx, task_id=10))
        self.assertNotIn("warning", result)

    def test_start_task_skips_branch_if_already_on_task_branch(self):
        client = _client()
        db = _tmp_db()
        client.execute.return_value = [{"id": 10, "name": "Fix VAT", "project_id": [5, "Acct"]}]
        ctx = self._ctx(_accepted(MagicMock(confirmed=True)))
        ctx.sample = AsyncMock()
        with (
            patch(_START_GUARD),
            patch(_SP_PATCH, _make_sp(current_branch="10#some-desc")),
            patch("odoo_sdk.commands.builtin.start_task.TaskStateDB", return_value=db),
            patch("odoo_sdk.commands.builtin.start_task.name_search_projects"),
            patch("odoo_sdk.commands.builtin.start_task.name_search_tasks"),
            patch("odoo_sdk.commands.builtin.start_task.get_employee_id", return_value=3),
            patch("odoo_sdk.commands.builtin.start_task.create_timesheet", return_value=1),
            patch("odoo_sdk.commands.builtin.start_task.post_chatter_note"),
        ):
            result = self._run(StartTaskCommand(client).execute("Fix VAT", ctx, task_id=10))
        ctx.sample.assert_not_called()
        # only one elicit call (the confirmation), not a second one for branch selection
        self.assertEqual(ctx.elicit.call_count, 1)
        self.assertNotIn("branch_name", result)

    def test_start_task_branch_selection_cancelled(self):
        client = _client()
        db = _tmp_db()
        ctx = self._ctx(
            _accepted(MagicMock(confirmed=True)),
            _cancelled(),
        )
        ctx.sample = AsyncMock()
        with (
            patch(_START_GUARD),
            patch(_SP_PATCH, _make_sp()),
            patch("odoo_sdk.commands.builtin.start_task.TaskStateDB", return_value=db),
            patch(
                "odoo_sdk.commands.builtin.start_task.name_search_projects",
                return_value=[{"id": 5, "name": "Acct"}],
            ),
            patch(
                "odoo_sdk.commands.builtin.start_task.name_search_tasks",
                return_value=[{"id": 10, "name": "Fix VAT"}],
            ),
        ):
            result = self._run(StartTaskCommand(client).execute("VAT", ctx))
        self.assertEqual(result, {"error": "Branch selection cancelled."})

    def test_start_task_auto_stashes_dirty_tree(self):
        client = _client()
        db = _tmp_db()
        client.execute.return_value = [{"id": 10, "name": "Fix VAT", "project_id": [5, "Acct"]}]
        ctx = self._ctx(_accepted(MagicMock(confirmed=True)), _accepted(MagicMock(selection=1)))
        ctx.sample = AsyncMock(return_value=MagicMock(text="fix-vat"))
        sp = _make_sp(dirty=True)
        with (
            patch(_START_GUARD),
            patch(_SP_PATCH, sp),
            patch("odoo_sdk.commands.builtin.start_task.TaskStateDB", return_value=db),
            patch("odoo_sdk.commands.builtin.start_task.name_search_projects"),
            patch("odoo_sdk.commands.builtin.start_task.name_search_tasks"),
            patch("odoo_sdk.commands.builtin.start_task.get_employee_id", return_value=3),
            patch("odoo_sdk.commands.builtin.start_task.create_timesheet", return_value=1),
            patch("odoo_sdk.commands.builtin.start_task.post_chatter_note"),
        ):
            self._run(StartTaskCommand(client).execute("Fix VAT", ctx, task_id=10))
        called = [c.args[0] for c in sp.run.call_args_list]
        self.assertTrue(any(c[:3] == ["git", "stash", "push"] for c in called))
        self.assertIn(["git", "stash", "pop"], called)


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
