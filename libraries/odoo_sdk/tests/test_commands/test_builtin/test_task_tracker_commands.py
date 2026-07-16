"""Tests for task-tracking Command subclasses."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from odoo_sdk.commands.builtin.get_task import GetTaskCommand
from odoo_sdk.commands.builtin.get_task_attachments import GetTaskAttachmentsCommand
from odoo_sdk.commands.builtin.get_task_chatter import GetTaskChatterCommand
from odoo_sdk.commands.builtin.resume_task import ResumeTaskCommand
from odoo_sdk.commands.builtin.search_projects import SearchProjectsCommand
from odoo_sdk.commands.builtin.search_tasks import SearchTasksCommand
from odoo_sdk.commands.builtin.start_task import StartTaskCommand
from odoo_sdk.commands.builtin.stop_task import StopTaskCommand
from odoo_sdk.commands.builtin.task_list import TaskListCommand
from odoo_sdk.commands.builtin.task_note import TaskNoteCommand
from odoo_sdk.commands.builtin.task_question import TaskQuestionCommand
from odoo_sdk.commands.builtin.task_status import TaskStatusCommand
from odoo_sdk.state import LocalStateClient as TaskStateDB
from odoo_sdk.state import TaskAlreadyRunningError, TaskNotRunningError, TaskState
from tests.support import make_state_db

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
    return make_state_db(Path(tmp.name))


def _client(uid: int = 7) -> MagicMock:
    c = MagicMock()
    c.uid = uid
    return c


def _cmd_with_db(cmd_cls, client, db):
    """Instantiate a command with an injected local state client (db)."""
    return cmd_cls(client, state=db)


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


# ── GetTaskAttachmentsCommand ─────────────────────────────────────────────────

class TestGetTaskAttachmentsCommand(unittest.TestCase):
    def test_delegates_to_helper(self):
        client = _client()
        expected = [{"id": 1, "name": "file.png", "source": "task"}]
        with patch(
            "odoo_sdk.commands.builtin.get_task_attachments.get_task_attachments",
            return_value=expected,
        ) as mock_helper:
            result = GetTaskAttachmentsCommand(client).execute(task_id=42)
        mock_helper.assert_called_once_with(client, 42, include_content=False)
        self.assertEqual(result, expected)

    def test_passes_include_content(self):
        client = _client()
        with patch(
            "odoo_sdk.commands.builtin.get_task_attachments.get_task_attachments",
            return_value=[],
        ) as mock_helper:
            GetTaskAttachmentsCommand(client).execute(
                task_id=10, include_content=True
            )
        mock_helper.assert_called_once_with(client, 10, include_content=True)


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

    def test_merges_chatter_when_requested(self):
        client = _client()
        task_data = {"task_id": 42, "name": "Feature X", "description": "Do it"}
        chatter_data = [{"id": 1, "author": "Jane", "body": "Note"}]
        with (
            patch("odoo_sdk.commands.builtin.get_task.get_task_detail", return_value=task_data),
            patch("odoo_sdk.commands.builtin.get_task.get_task_chatter", return_value=chatter_data),
        ):
            result = GetTaskCommand(client).execute(task_id=42, include=["chatter"])
        self.assertEqual(result["chatter"], chatter_data)
        self.assertEqual(result["name"], "Feature X")

    def test_default_does_not_fetch_chatter(self):
        client = _client()
        task_data = {"task_id": 42, "name": "Feature X", "description": "Do it"}
        with (
            patch(
                "odoo_sdk.commands.builtin.get_task.get_task_detail",
                return_value=task_data,
            ) as mock_detail,
            patch("odoo_sdk.commands.builtin.get_task.get_task_chatter") as mock_chatter,
        ):
            result = GetTaskCommand(client).execute(task_id=42)
        self.assertNotIn("chatter", result)
        mock_chatter.assert_not_called()
        mock_detail.assert_called_once_with(client, 42, include=None)

    def test_forwards_include_to_get_task_detail(self):
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
            GetTaskCommand(client).execute(task_id=7, include=["subtasks", "chatter"])
        mock_detail.assert_called_once_with(
            client, 7, include=["subtasks", "chatter"]
        )
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
        ):
            result = _cmd_with_db(TaskStatusCommand, _client(), db).execute()
        self.assertEqual(result, [])

    def test_returns_active_sessions(self):
        db = _tmp_db()
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=1)
        db.create_run(2, "Feature", 10, "Project A", timesheet_id=2)
        with (
            patch(_STATUS_GUARD),
        ):
            result = _cmd_with_db(TaskStatusCommand, _client(), db).execute()
        self.assertEqual(len(result), 2)
        task_ids = {r["task_id"] for r in result}
        self.assertEqual(task_ids, {1, 2})

    def test_result_contains_required_keys(self):
        db = _tmp_db()
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=1)
        with (
            patch(_STATUS_GUARD),
        ):
            result = _cmd_with_db(TaskStatusCommand, _client(), db).execute()
        self.assertIn("elapsed", result[0])
        self.assertIn("state", result[0])
        self.assertIn("started_at", result[0])


# ── TaskNoteCommand ───────────────────────────────────────────────────────────

class TestTaskNoteCommand(unittest.TestCase):
    def test_posts_note_and_appends_to_run(self):
        client = _client()
        db = _tmp_db()
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=1)
        with (
            patch(_NOTE_GUARD),
            patch(
                "odoo_sdk.commands.builtin.task_note.post_chatter_note",
                return_value=55,
            ) as mock_post,
        ):
            result = _cmd_with_db(TaskNoteCommand, client, db).execute(1, "Note text")
        mock_post.assert_called_once_with(client, 1, "Note text")
        self.assertEqual(result["message_id"], 55)
        run = db.get_active_run(1)
        self.assertIn("Note text", run.notes)  # type: ignore[union-attr]

    def test_raises_when_no_active_session(self):
        db = _tmp_db()
        with (
            patch(_NOTE_GUARD),
        ):
            with self.assertRaises(TaskNotRunningError):
                _cmd_with_db(TaskNoteCommand, _client(), db).execute(999, "note")

    def test_response_carries_checkpoint_hint(self):
        client = _client()
        db = _tmp_db()
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=1)
        with (
            patch(_NOTE_GUARD),
            patch(
                "odoo_sdk.commands.builtin.task_note.post_chatter_note",
                return_value=55,
            ),
        ):
            result = _cmd_with_db(TaskNoteCommand, client, db).execute(1, "Note text")
        self.assertIn("minutes_since_last_note", result)
        self.assertIn("suggest_checkpoint", result)
        self.assertFalse(result["suggest_checkpoint"])

    def test_hint_suggests_checkpoint_after_stale_note(self):
        from datetime import datetime, timedelta, timezone

        from odoo_sdk.state import EventRecord

        client = _client()
        db = _tmp_db()
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=1)
        db.add_event(
            EventRecord(
                id=None,
                source="agent",
                timestamp=datetime.now(timezone.utc) - timedelta(minutes=25),
                task_ids=["1"],
                repo="",
                subject="task_note",
                payload={"tool": "task_note"},
            )
        )
        with (
            patch(_NOTE_GUARD),
            patch(
                "odoo_sdk.commands.builtin.task_note.post_chatter_note",
                return_value=55,
            ),
        ):
            result = _cmd_with_db(TaskNoteCommand, client, db).execute(1, "Note text")
        self.assertEqual(result["minutes_since_last_note"], 25)
        self.assertTrue(result["suggest_checkpoint"])


# ── TaskQuestionCommand ───────────────────────────────────────────────────────

class TestTaskQuestionCommand(unittest.TestCase):
    def test_posts_prefixed_question_and_transitions(self):
        client = _client()
        db = _tmp_db()
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=1)
        with (
            patch(_QUESTION_GUARD),
            patch(
                "odoo_sdk.commands.builtin.task_question.post_chatter_note",
                return_value=77,
            ) as mock_post,
        ):
            result = _cmd_with_db(TaskQuestionCommand, client, db).execute(1, "Which approach?")
        mock_post.assert_called_once_with(client, 1, "[?] Which approach?")
        self.assertEqual(result["state"], "AWAITING_ANSWERS")
        self.assertEqual(result["message_id"], 77)

    def test_self_loop_on_awaiting_answers(self):
        client = _client()
        db = _tmp_db()
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=1)
        db.transition_to_awaiting(1)
        with (
            patch(_QUESTION_GUARD),
            patch("odoo_sdk.commands.builtin.task_question.post_chatter_note", return_value=78),
        ):
            result = _cmd_with_db(TaskQuestionCommand, client, db).execute(1, "Another question?")
        self.assertEqual(result["state"], "AWAITING_ANSWERS")

    def test_raises_when_no_active_session(self):
        db = _tmp_db()
        with (
            patch(_QUESTION_GUARD),
        ):
            with self.assertRaises(TaskNotRunningError):
                _cmd_with_db(TaskQuestionCommand, _client(), db).execute(999, "?")


# ── ResumeTaskCommand ─────────────────────────────────────────────────────────

class TestResumeTaskCommand(unittest.TestCase):
    def test_transitions_and_posts_note(self):
        client = _client()
        db = _tmp_db()
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=1)
        db.transition_to_awaiting(1)
        with (
            patch(_RESUME_GUARD),
            patch(
                "odoo_sdk.commands.builtin.resume_task.post_chatter_note",
                return_value=88,
            ) as mock_post,
        ):
            result = _cmd_with_db(ResumeTaskCommand, client, db).execute(1)
        mock_post.assert_called_once()
        chatter_body = mock_post.call_args.args[2]
        self.assertIn("Resuming", chatter_body)
        self.assertEqual(result["state"], "RUNNING")
        self.assertIn("resumed_at", result)

    def test_raises_when_running(self):
        db = _tmp_db()
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=1)
        from odoo_sdk.state import InvalidStateTransitionError
        with (
            patch(_RESUME_GUARD),
        ):
            with self.assertRaises(InvalidStateTransitionError):
                _cmd_with_db(ResumeTaskCommand, _client(), db).execute(1)



# ── SearchProjectsCommand ─────────────────────────────────────────────────────

class TestSearchProjectsCommand(unittest.TestCase):
    def test_delegates_to_name_search_projects(self):
        client = _client()
        with patch(
            "odoo_sdk.commands.builtin.search_projects.name_search_projects",
            return_value=[{"id": 5, "name": "Accounting"}],
        ) as mock_search:
            result = SearchProjectsCommand(client).execute("Acc")
        mock_search.assert_called_once_with(client, "Acc", limit=10)
        self.assertEqual(result, [{"id": 5, "name": "Accounting"}])

    def test_passes_custom_limit(self):
        client = _client()
        with patch(
            "odoo_sdk.commands.builtin.search_projects.name_search_projects",
            return_value=[],
        ) as mock_search:
            SearchProjectsCommand(client).execute("x", limit=3)
        mock_search.assert_called_once_with(client, "x", limit=3)

    def test_returns_empty_list_when_no_matches(self):
        client = _client()
        with patch(
            "odoo_sdk.commands.builtin.search_projects.name_search_projects",
            return_value=[],
        ):
            self.assertEqual(SearchProjectsCommand(client).execute("nope"), [])


# ── SearchTasksCommand ────────────────────────────────────────────────────────

class TestSearchTasksCommand(unittest.TestCase):
    def test_delegates_to_name_search_tasks_with_project_scope(self):
        client = _client()
        with patch(
            "odoo_sdk.commands.builtin.search_tasks.name_search_tasks",
            return_value=[{"id": 10, "name": "Fix VAT"}],
        ) as mock_search:
            result = SearchTasksCommand(client).execute("VAT", project_id=5)
        mock_search.assert_called_once_with(client, "VAT", 5, limit=10)
        self.assertEqual(result, [{"id": 10, "name": "Fix VAT"}])

    def test_passes_custom_limit(self):
        client = _client()
        with patch(
            "odoo_sdk.commands.builtin.search_tasks.name_search_tasks",
            return_value=[],
        ) as mock_search:
            SearchTasksCommand(client).execute("x", project_id=1, limit=2)
        mock_search.assert_called_once_with(client, "x", 1, limit=2)


# ── StartTaskCommand ──────────────────────────────────────────────────────────

class TestStartTaskCommand(unittest.TestCase):
    def _start(self, client, db, **kwargs):
        with (
            patch(_START_GUARD),
            patch("odoo_sdk.commands.builtin.start_task.post_chatter_note") as mock_note,
        ):
            result = _cmd_with_db(StartTaskCommand, client, db).execute(**kwargs)
        return result, mock_note

    def _base_kwargs(self, **overrides):
        kwargs = {
            "task_id": 10,
            "task_name": "Fix VAT",
            "project_id": 5,
            "project_name": "Accounting",
        }
        kwargs.update(overrides)
        return kwargs

    def test_creates_run_and_writes_no_timesheet(self):
        client = _client()
        db = _tmp_db()
        result, mock_note = self._start(client, db, **self._base_kwargs())
        self.assertEqual(result["task_id"], 10)
        self.assertEqual(result["task_name"], "Fix VAT")
        self.assertEqual(result["project_name"], "Accounting")
        # No anchor is created (#325): the FSM writes no account.analytic.line,
        # so timesheet_id stays None until the upload path materializes hours.
        self.assertIsNone(result["timesheet_id"])
        self.assertIn("run_id", result)
        mock_note.assert_called_once_with(client, 10, "Work started on this task.")
        self.assertIsNotNone(db.get_active_run(10))
        # The command body makes no Odoo call other than the chatter note (mocked).
        client.execute.assert_not_called()

    def test_response_carries_checkpoint_hint_at_zero(self):
        client = _client()
        db = _tmp_db()
        result, _ = self._start(client, db, **self._base_kwargs())
        self.assertEqual(result["minutes_since_last_note"], 0)
        self.assertFalse(result["suggest_checkpoint"])

    def test_echoes_branch_name_and_warning(self):
        client = _client()
        db = _tmp_db()
        result, _ = self._start(
            client, db, **self._base_kwargs(branch_name="10#fix-vat", warning="heads up")
        )
        self.assertEqual(result["branch_name"], "10#fix-vat")
        self.assertEqual(result["warning"], "heads up")

    def test_no_branch_or_warning_keys_when_absent(self):
        client = _client()
        db = _tmp_db()
        result, _ = self._start(client, db, **self._base_kwargs())
        self.assertNotIn("branch_name", result)
        self.assertNotIn("warning", result)

    def test_raises_when_already_active(self):
        client = _client()
        db = _tmp_db()
        db.create_run(10, "Fix VAT", 5, "Accounting", timesheet_id=1)
        existing = db.get_active_run(10)
        with self.assertRaises(TaskAlreadyRunningError) as ctx:
            self._start(client, db, **self._base_kwargs())
        self.assertEqual(
            str(ctx.exception),
            f"Task 'Fix VAT' already has an active session "
            f"(id={existing.id}, state={existing.state.value}).",
        )

    def test_run_insert_failure_reraises(self):
        # Record deletion (unlink) is purposefully not implemented, so a run
        # insert failure re-raises loudly rather than attempting any rollback.
        # The chatter note is never posted because the failure short-circuits
        # before it, and the FSM makes no Odoo call of its own (#325).
        client = _client()
        db = MagicMock()
        db.get_active_run.return_value = None
        db.create_run.side_effect = RuntimeError("insert failed")
        with (
            patch(_START_GUARD),
            patch("odoo_sdk.commands.builtin.start_task.post_chatter_note") as mock_note,
        ):
            with self.assertRaises(RuntimeError):
                _cmd_with_db(StartTaskCommand, client, db).execute(**self._base_kwargs())
        # No account.analytic.line write is attempted, and the note is skipped.
        client.execute.assert_not_called()
        mock_note.assert_not_called()

    def test_order_is_run_then_note(self):
        # The FSM creates the local run first, then best-effort posts the chatter
        # note — no timesheet step exists between them any more (#325).
        client = _client()
        db = _tmp_db()
        order: list[str] = []
        real_create_run = db.create_run

        def _tracked_create_run(*args, **kwargs):
            order.append("run")
            return real_create_run(*args, **kwargs)

        with (
            patch(_START_GUARD),
            patch(
                "odoo_sdk.commands.builtin.start_task.post_chatter_note",
                side_effect=lambda *a, **k: order.append("note"),
            ),
            patch.object(db, "create_run", side_effect=_tracked_create_run),
        ):
            _cmd_with_db(StartTaskCommand, client, db).execute(**self._base_kwargs())
        self.assertEqual(order, ["run", "note"])

    def test_chatter_failure_is_best_effort_and_leaves_one_running_run(self):
        # The chatter note is a best-effort announcement (issue #361): if the
        # post raises after the run is already created, start_task must still
        # return success and leave exactly one RUNNING run. A bare failure would
        # error out with the run RUNNING, so a retry would hit
        # TaskAlreadyRunningError and wedge the caller.
        client = _client()
        db = _tmp_db()
        with (
            patch(_START_GUARD),
            patch(
                "odoo_sdk.commands.builtin.start_task.post_chatter_note",
                side_effect=RuntimeError("Odoo unreachable"),
            ) as mock_note,
        ):
            result = _cmd_with_db(StartTaskCommand, client, db).execute(
                **self._base_kwargs()
            )
        mock_note.assert_called_once_with(client, 10, "Work started on this task.")
        # The command reports success with the full run result.
        self.assertEqual(result["task_id"], 10)
        self.assertIsNone(result["timesheet_id"])
        self.assertIn("run_id", result)
        # Exactly one run exists and it is RUNNING — a retry-safe end state.
        runs = db.get_all_runs()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].state, TaskState.RUNNING)
        active = db.get_active_run(10)
        self.assertIsNotNone(active)
        self.assertEqual(active.id, result["run_id"])

    def test_chatter_failure_does_not_block_retry_safety(self):
        # After a swallowed chatter failure the run is RUNNING, so a naive retry
        # of the same task must raise TaskAlreadyRunningError rather than create
        # a second run — proving the swallow keeps the invariant the guard
        # depends on.
        client = _client()
        db = _tmp_db()
        with (
            patch(_START_GUARD),
            patch(
                "odoo_sdk.commands.builtin.start_task.post_chatter_note",
                side_effect=RuntimeError("Odoo unreachable"),
            ),
        ):
            _cmd_with_db(StartTaskCommand, client, db).execute(**self._base_kwargs())
            with self.assertRaises(TaskAlreadyRunningError):
                _cmd_with_db(StartTaskCommand, client, db).execute(
                    **self._base_kwargs()
                )
        self.assertEqual(len(db.get_all_runs()), 1)


# ── StopTaskCommand ───────────────────────────────────────────────────────────

class TestStopTaskCommand(unittest.TestCase):
    def test_stops_run_and_writes_no_timesheet(self):
        client = _client()
        db = _tmp_db()
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=50)
        with patch(_STOP_GUARD):
            result = _cmd_with_db(StopTaskCommand, client, db).execute(1, "Fixed the bug")
        # stop_task no longer writes hours to Odoo (#325): the anchor is left for
        # the TUI/ETL upload path to close out, so the command touches no
        # account.analytic.line row at all.
        client.execute.assert_not_called()
        # The result shape is unchanged: elapsed hours are still computed and
        # returned for callers/tests to display.
        self.assertIn("elapsed", result)
        self.assertIn("elapsed_hours", result)
        self.assertIn("[/]", result["description"])
        from odoo_sdk.state import TaskState
        run = db.get_run_by_id(result["run_id"])
        self.assertEqual(run.state, TaskState.STOPPED)

    def test_description_not_double_prefixed(self):
        client = _client()
        db = _tmp_db()
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=50)
        with patch(_STOP_GUARD):
            result = _cmd_with_db(StopTaskCommand, client, db).execute(1, "[/] Already prefixed")
        self.assertTrue(result["description"].startswith("[/]"))
        self.assertFalse(result["description"].startswith("[/] [/]"))

    def test_raises_when_no_active_session(self):
        db = _tmp_db()
        with patch(_STOP_GUARD):
            with self.assertRaises(TaskNotRunningError):
                _cmd_with_db(StopTaskCommand, _client(), db).execute(999, "desc")

    def test_stop_from_awaiting_answers(self):
        client = _client()
        db = _tmp_db()
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=50)
        db.transition_to_awaiting(1)
        with patch(_STOP_GUARD):
            result = _cmd_with_db(StopTaskCommand, client, db).execute(1, "done")
        from odoo_sdk.state import TaskState
        run = db.get_run_by_id(result["run_id"])
        self.assertEqual(run.state, TaskState.STOPPED)

    def test_no_timesheet_write_even_without_timesheet_id(self):
        # With or without a known anchor id, stop_task writes nothing to Odoo:
        # hours are the exclusive job of the TUI/ETL upload path (#325).
        client = _client()
        db = _tmp_db()
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=None)
        with patch(_STOP_GUARD):
            _cmd_with_db(StopTaskCommand, client, db).execute(1, "done")
        client.execute.assert_not_called()


# ── AGENT event production moved to the MCP wrapper (issue #326) ───────────────

class TestNoAgentEventFromCommandBody(unittest.TestCase):
    """FSM command bodies no longer emit AGENT events themselves (#326).

    Emission was consolidated into the generic ``_event_emitting`` wrapper in
    :mod:`odoo_sdk.mcp.server`, which became the *sole* producer for the MCP tool
    surface. Executing a command directly (bypassing the server) must therefore
    write no ``agent`` event; these tests pin that the internal
    ``emit_agent_event`` calls were removed from the command bodies.
    """

    def _assert_no_agent_event(self, db):
        events = db.get_events()
        agent = [e for e in events if e.source == "agent"]
        self.assertEqual(agent, [])

    def test_start_task_emits_no_agent_event(self):
        client = _client()
        db = _tmp_db()
        with (
            patch(_START_GUARD),
            patch("odoo_sdk.commands.builtin.start_task.post_chatter_note"),
        ):
            _cmd_with_db(StartTaskCommand, client, db).execute(
                task_id=10, task_name="Fix", project_id=5, project_name="Acct"
            )
        self._assert_no_agent_event(db)

    def test_stop_task_emits_no_agent_event(self):
        client = _client()
        db = _tmp_db()
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=50)
        with patch(_STOP_GUARD):
            _cmd_with_db(StopTaskCommand, client, db).execute(1, "done")
        self._assert_no_agent_event(db)

    def test_task_note_emits_no_agent_event(self):
        client = _client()
        db = _tmp_db()
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=1)
        with (
            patch(_NOTE_GUARD),
            patch("odoo_sdk.commands.builtin.task_note.post_chatter_note", return_value=1),
        ):
            _cmd_with_db(TaskNoteCommand, client, db).execute(1, "progress note")
        self._assert_no_agent_event(db)

    def test_task_question_emits_no_agent_event(self):
        client = _client()
        db = _tmp_db()
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=1)
        with (
            patch(_QUESTION_GUARD),
            patch("odoo_sdk.commands.builtin.task_question.post_chatter_note", return_value=1),
        ):
            _cmd_with_db(TaskQuestionCommand, client, db).execute(1, "which approach?")
        self._assert_no_agent_event(db)

    def test_resume_task_emits_no_agent_event(self):
        client = _client()
        db = _tmp_db()
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=1)
        db.transition_to_awaiting(1)
        with (
            patch(_RESUME_GUARD),
            patch("odoo_sdk.commands.builtin.resume_task.post_chatter_note", return_value=1),
        ):
            _cmd_with_db(ResumeTaskCommand, client, db).execute(1)
        self._assert_no_agent_event(db)


if __name__ == "__main__":
    unittest.main()
