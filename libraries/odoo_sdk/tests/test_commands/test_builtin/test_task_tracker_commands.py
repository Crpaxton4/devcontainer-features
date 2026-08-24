"""Tests for task-tracking Command subclasses."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from odoo_sdk.commands.builtin.close_task import CloseTaskCommand
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


def _add_agent_event(db, task_id, subject, timestamp=None):
    """Record one attributed agent event for the run-summary derivation (#626)."""
    from datetime import datetime, timezone

    from odoo_sdk.state import EventRecord

    db.add_event(
        EventRecord(
            id=None,
            source="agent",
            timestamp=timestamp or datetime.now(timezone.utc),
            task_ids=[task_id],
            repo="",
            subject=subject,
        )
    )


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
        mock_post.assert_called_once_with(client, 1, "Note text", attachment_ids=None)
        self.assertEqual(result["message_id"], 55)
        self.assertNotIn("attachment_ids", result)
        run = db.get_active_run(1)
        self.assertIn("Note text", run.notes)  # type: ignore[union-attr]

    def test_attachments_created_and_linked_to_post(self):
        # #604: file specs become ir.attachment records linked to the task,
        # and their ids ride to ``post_chatter_note`` / the result payload.
        client = _client()
        db = _tmp_db()
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=1)
        specs = [{"content": "aGk=", "name": "a.md"}, {"path": "/tmp/b.csv"}]
        with (
            patch(_NOTE_GUARD),
            patch(
                "odoo_sdk.commands.builtin.task_note.create_attachments",
                return_value=[91, 92],
            ) as mock_create,
            patch(
                "odoo_sdk.commands.builtin.task_note.post_chatter_note",
                return_value=55,
            ) as mock_post,
        ):
            result = _cmd_with_db(TaskNoteCommand, client, db).execute(
                1, "Deliverables attached", attachments=specs
            )
        mock_create.assert_called_once_with(
            client, specs, res_model="project.task", res_id=1
        )
        mock_post.assert_called_once_with(
            client, 1, "Deliverables attached", attachment_ids=[91, 92]
        )
        self.assertEqual(result["attachment_ids"], [91, 92])
        self.assertEqual(result["message_id"], 55)

    def test_rejects_note_over_300_chars(self):
        # #610: an over-limit note is rejected — nothing is uploaded, posted,
        # or appended to the local session log.
        client = _client()
        db = _tmp_db()
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=1)
        with (
            patch(_NOTE_GUARD),
            patch(
                "odoo_sdk.commands.builtin.task_note.post_chatter_note"
            ) as mock_post,
        ):
            with self.assertRaises(ValueError) as ctx:
                _cmd_with_db(TaskNoteCommand, client, db).execute(1, "x" * 301)
        message = str(ctx.exception)
        self.assertIn("301", message)
        self.assertIn("300", message)
        self.assertIn("simple, direct, plain", message)
        mock_post.assert_not_called()
        run = db.get_active_run(1)
        self.assertNotIn("x" * 301, run.notes or "")  # type: ignore[union-attr]

    def test_accepts_note_of_exactly_300_chars(self):
        client = _client()
        db = _tmp_db()
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=1)
        note = "y" * 300
        with (
            patch(_NOTE_GUARD),
            patch(
                "odoo_sdk.commands.builtin.task_note.post_chatter_note",
                return_value=56,
            ) as mock_post,
        ):
            result = _cmd_with_db(TaskNoteCommand, client, db).execute(1, note)
        mock_post.assert_called_once_with(client, 1, note, attachment_ids=None)
        self.assertEqual(result["message_id"], 56)

    def test_description_advertises_the_300_char_limit(self):
        # The limit must be visible to MCP callers up front: the command
        # description (which becomes the tool description) names it.
        self.assertIn("300", TaskNoteCommand._description)

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

    def test_local_append_commits_before_chatter_post(self):
        # #627 ordering: by the time the chatter post fires, the note is
        # already committed locally — a note visible in Odoo therefore always
        # implies a note present in the session log.
        client = _client()
        db = _tmp_db()
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=1)
        notes_at_post_time: list = []

        def capture_local_state(*args, **kwargs):
            run = db.get_active_run(1)
            notes_at_post_time.extend(run.notes)  # type: ignore[union-attr]
            return 55

        with (
            patch(_NOTE_GUARD),
            patch(
                "odoo_sdk.commands.builtin.task_note.post_chatter_note",
                side_effect=capture_local_state,
            ),
        ):
            _cmd_with_db(TaskNoteCommand, client, db).execute(1, "Ordered note")
        self.assertEqual(notes_at_post_time, ["Ordered note"])

    def test_failed_post_keeps_local_note_and_raises(self):
        # #627 documented tradeoff: when the chatter post fails AFTER the local
        # append committed, the error surfaces to the caller (detectable, they
        # retry) and the local note remains — a benign duplicate on retry beats
        # a chatter note missing from the session log.
        client = _client()
        db = _tmp_db()
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=1)
        with (
            patch(_NOTE_GUARD),
            patch(
                "odoo_sdk.commands.builtin.task_note.post_chatter_note",
                side_effect=RuntimeError("odoo unreachable"),
            ),
        ):
            with self.assertRaises(RuntimeError):
                _cmd_with_db(TaskNoteCommand, client, db).execute(1, "Kept note")
        run = db.get_active_run(1)
        self.assertEqual(run.notes, ["Kept note"])  # type: ignore[union-attr]

    def test_session_stopped_mid_call_blocks_chatter_post(self):
        # #627 session-stop race: the session dies between the entry guard and
        # the local append (simulated inside the attachment step). The append's
        # own single-UPDATE check detects it, so the call fails BEFORE anything
        # posts to chatter — never chatter-posted-but-locally-lost.
        client = _client()
        db = _tmp_db()
        created = db.create_run(1, "Bug", 10, "Project A", timesheet_id=1)

        def stop_session_then_return_ids(*args, **kwargs):
            db.stop_run(1)
            return [91]

        with (
            patch(_NOTE_GUARD),
            patch(
                "odoo_sdk.commands.builtin.task_note.create_attachments",
                side_effect=stop_session_then_return_ids,
            ),
            patch(
                "odoo_sdk.commands.builtin.task_note.post_chatter_note"
            ) as mock_post,
        ):
            with self.assertRaises(TaskNotRunningError):
                _cmd_with_db(TaskNoteCommand, client, db).execute(
                    1, "Raced note", attachments=[{"path": "/tmp/a.md"}]
                )
        mock_post.assert_not_called()
        stopped = db.get_run_by_id(created.id)
        self.assertEqual(stopped.notes, [])  # type: ignore[union-attr]


# ── Unified active-session guard (#627) ───────────────────────────────────────

class TestUnifiedActiveSessionGuard(unittest.TestCase):
    def test_command_and_db_guards_share_one_message(self):
        from odoo_sdk.commands.command import require_active_run

        db = _tmp_db()
        with self.assertRaises(TaskNotRunningError) as cmd_ctx:
            require_active_run(db, 999)
        with self.assertRaises(TaskNotRunningError) as db_ctx:
            db.require_active_run(999)
        self.assertEqual(str(cmd_ctx.exception), str(db_ctx.exception))
        self.assertEqual(str(cmd_ctx.exception), "No active session for task 999.")

    def test_guard_returns_the_active_run(self):
        db = _tmp_db()
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=1)
        run = db.require_active_run(1)
        self.assertEqual(run.task_id, 1)
        self.assertEqual(run.state, TaskState.RUNNING)


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

    def test_rejects_question_over_300_chars(self):
        # #610: an over-limit question is rejected before anything is posted
        # and before the session leaves RUNNING.
        client = _client()
        db = _tmp_db()
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=1)
        with (
            patch(_QUESTION_GUARD),
            patch(
                "odoo_sdk.commands.builtin.task_question.post_chatter_note"
            ) as mock_post,
        ):
            with self.assertRaises(ValueError) as ctx:
                _cmd_with_db(TaskQuestionCommand, client, db).execute(
                    1, "q" * 301
                )
        message = str(ctx.exception)
        self.assertIn("300", message)
        self.assertIn("simple, direct, plain", message)
        mock_post.assert_not_called()
        run = db.get_active_run(1)
        self.assertEqual(run.state, TaskState.RUNNING)  # type: ignore[union-attr]

    def test_accepts_question_of_exactly_300_chars(self):
        client = _client()
        db = _tmp_db()
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=1)
        question = "q" * 300
        with (
            patch(_QUESTION_GUARD),
            patch(
                "odoo_sdk.commands.builtin.task_question.post_chatter_note",
                return_value=79,
            ) as mock_post,
        ):
            result = _cmd_with_db(TaskQuestionCommand, client, db).execute(
                1, question
            )
        mock_post.assert_called_once_with(client, 1, f"[?] {question}")
        self.assertEqual(result["state"], "AWAITING_ANSWERS")

    def test_description_advertises_the_300_char_limit(self):
        self.assertIn("300", TaskQuestionCommand._description)


# ── CloseTaskCommand ──────────────────────────────────────────────────────────

class TestCloseTaskCommand(unittest.TestCase):
    def test_closes_a_running_run(self):
        client = _client()
        db = _tmp_db()
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=1)
        result = _cmd_with_db(CloseTaskCommand, client, db).execute(1)
        self.assertTrue(result["closed"])
        self.assertEqual(result["state"], "CLOSED")
        self.assertEqual(result["task_id"], 1)
        self.assertIsNotNone(result["run_id"])
        # Terminal: the closed run vanishes from the active and default surfaces.
        self.assertIsNone(db.get_active_run(1))
        self.assertEqual(db.get_all_runs(), [])

    def test_closes_a_stopped_run(self):
        client = _client()
        db = _tmp_db()
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=1)
        db.stop_run(1)
        result = _cmd_with_db(CloseTaskCommand, client, db).execute(1)
        self.assertTrue(result["closed"])
        self.assertEqual(result["state"], "CLOSED")
        self.assertIsNone(db.get_resumable_run(1))

    def test_reports_nothing_to_close(self):
        client = _client()
        db = _tmp_db()
        result = _cmd_with_db(CloseTaskCommand, client, db).execute(999)
        self.assertFalse(result["closed"])
        self.assertIsNone(result["run_id"])
        self.assertIsNone(result["state"])

    def test_makes_no_odoo_call(self):
        # Purely local FSM transition — no timesheet write, no chatter.
        client = _client()
        db = _tmp_db()
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=1)
        _cmd_with_db(CloseTaskCommand, client, db).execute(1)
        client.execute.assert_not_called()

    def test_registered_as_builtin_but_invisible_to_mcp(self):
        # The whole point of CLOSED (#504): close_task is a real builtin the CLI
        # can dispatch, but it has NO tool factory, so the MCP wire surface never
        # exposes it and the agent cannot reach the terminal state.
        from odoo_sdk.commands.builtin import BUILTIN_COMMANDS
        from odoo_sdk.mcp.tools import TOOL_FACTORIES

        self.assertIn("close_task", BUILTIN_COMMANDS)
        self.assertIs(BUILTIN_COMMANDS["close_task"], CloseTaskCommand)
        self.assertNotIn("close_task", TOOL_FACTORIES)


# ── ResumeTaskCommand ─────────────────────────────────────────────────────────

class TestResumeTaskCommand(unittest.TestCase):
    def test_transitions_to_running(self):
        client = _client()
        db = _tmp_db()
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=1)
        db.transition_to_awaiting(1)
        with patch(_RESUME_GUARD):
            result = _cmd_with_db(ResumeTaskCommand, client, db).execute(1)
        self.assertEqual(result["state"], "RUNNING")
        self.assertIn("resumed_at", result)

    def test_posts_no_chatter_note(self):
        # The contentless "Resuming implementation…" marker is gone (#505):
        # the transition and its event row are the whole record, so the
        # command makes no Odoo call at all.
        client = _client()
        db = _tmp_db()
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=1)
        db.transition_to_awaiting(1)
        with patch(_RESUME_GUARD):
            _cmd_with_db(ResumeTaskCommand, client, db).execute(1)
        client.execute.assert_not_called()

    def test_raises_when_running(self):
        db = _tmp_db()
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=1)
        from odoo_sdk.state import InvalidStateTransitionError
        with (
            patch(_RESUME_GUARD),
        ):
            with self.assertRaises(InvalidStateTransitionError):
                _cmd_with_db(ResumeTaskCommand, _client(), db).execute(1)

    def test_resumes_a_stopped_session(self):
        # STOPPED is now resumable (#504): resume reopens the paused run.
        client = _client()
        db = _tmp_db()
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=1)
        db.stop_run(1)
        with patch(_RESUME_GUARD):
            result = _cmd_with_db(ResumeTaskCommand, client, db).execute(1)
        self.assertEqual(result["state"], "RUNNING")
        self.assertIsNotNone(db.get_active_run(1))



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
        with patch(_START_GUARD):
            return _cmd_with_db(StartTaskCommand, client, db).execute(**kwargs)

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
        result = self._start(client, db, **self._base_kwargs())
        self.assertEqual(result["task_id"], 10)
        self.assertEqual(result["task_name"], "Fix VAT")
        self.assertEqual(result["project_name"], "Accounting")
        # No anchor is created (#325): the FSM writes no account.analytic.line,
        # so timesheet_id stays None until the upload path materializes hours.
        self.assertIsNone(result["timesheet_id"])
        self.assertIn("run_id", result)
        self.assertIsNotNone(db.get_active_run(10))
        # The command body makes no Odoo call at all — no timesheet write
        # (#325) and no chatter note (#505).
        client.execute.assert_not_called()

    def test_response_carries_checkpoint_hint_at_zero(self):
        client = _client()
        db = _tmp_db()
        result = self._start(client, db, **self._base_kwargs())
        self.assertEqual(result["minutes_since_last_note"], 0)
        self.assertFalse(result["suggest_checkpoint"])

    def test_echoes_branch_name_and_warning(self):
        client = _client()
        db = _tmp_db()
        result = self._start(
            client, db, **self._base_kwargs(branch_name="10#fix-vat", warning="heads up")
        )
        self.assertEqual(result["branch_name"], "10#fix-vat")
        self.assertEqual(result["warning"], "heads up")

    def test_no_branch_or_warning_keys_when_absent(self):
        client = _client()
        db = _tmp_db()
        result = self._start(client, db, **self._base_kwargs())
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
        # The FSM makes no Odoo call of its own (#325, #505).
        client = _client()
        db = MagicMock()
        db.get_active_run.return_value = None
        # No resumable stopped run, so the command takes the create path (#504).
        db.get_resumable_run.return_value = None
        db.create_run.side_effect = RuntimeError("insert failed")
        with patch(_START_GUARD):
            with self.assertRaises(RuntimeError):
                _cmd_with_db(StartTaskCommand, client, db).execute(**self._base_kwargs())
        # No account.analytic.line write is attempted.
        client.execute.assert_not_called()

    def test_successful_start_leaves_one_running_run(self):
        # The run is the whole side effect: a successful start must leave
        # exactly one RUNNING run, and it must be the one reported back.
        client = _client()
        db = _tmp_db()
        result = self._start(client, db, **self._base_kwargs())
        runs = db.get_all_runs()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].state, TaskState.RUNNING)
        active = db.get_active_run(10)
        self.assertIsNotNone(active)
        self.assertEqual(active.id, result["run_id"])

    def test_retry_of_running_task_creates_no_second_run(self):
        # A naive retry of the same task raises TaskAlreadyRunningError rather
        # than creating a second run — the guard's invariant (issue #361).
        client = _client()
        db = _tmp_db()
        self._start(client, db, **self._base_kwargs())
        with self.assertRaises(TaskAlreadyRunningError):
            self._start(client, db, **self._base_kwargs())
        self.assertEqual(len(db.get_all_runs()), 1)

    def test_start_after_stop_auto_resumes_same_run(self):
        # Auto-resume (#504): starting a task whose latest run is STOPPED reopens
        # that SAME row (original started_at preserved) instead of inserting a
        # second one, so one continuous effort stays one run.
        client = _client()
        db = _tmp_db()
        first = self._start(client, db, **self._base_kwargs())
        db.stop_run(10)
        resumed = self._start(client, db, **self._base_kwargs())
        self.assertEqual(resumed["run_id"], first["run_id"])
        self.assertEqual(len(db.get_all_runs()), 1)
        active = db.get_active_run(10)
        self.assertIsNotNone(active)
        self.assertEqual(active.state, TaskState.RUNNING)  # type: ignore[union-attr]

    def test_start_after_abort_creates_fresh_run(self):
        # An aborted (voided) run is never auto-resumed: a fresh start opens a new
        # run so the discarded time cannot be resurrected.
        client = _client()
        db = _tmp_db()
        first = self._start(client, db, **self._base_kwargs())
        db.abort_run(10)
        second = self._start(client, db, **self._base_kwargs())
        self.assertNotEqual(second["run_id"], first["run_id"])


# ── StopTaskCommand ───────────────────────────────────────────────────────────

class TestStopTaskCommand(unittest.TestCase):
    def test_stops_run_and_writes_no_timesheet(self):
        client = _client()
        db = _tmp_db()
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=50)
        with patch(_STOP_GUARD):
            result = _cmd_with_db(StopTaskCommand, client, db).execute(1)
        # stop_task no longer writes hours to Odoo (#325): the anchor is left for
        # the TUI/ETL upload path to close out, so the command touches no
        # account.analytic.line row at all.
        client.execute.assert_not_called()
        # Elapsed hours are still computed and returned for callers to display.
        self.assertIn("elapsed", result)
        self.assertIn("elapsed_hours", result)
        from odoo_sdk.state import TaskState
        run = db.get_run_by_id(result["run_id"])
        self.assertEqual(run.state, TaskState.STOPPED)

    def test_takes_no_description_parameter(self):
        # #623: the description parameter was a no-op gate with zero downstream
        # consumers; the command signature is task_id alone.
        import inspect

        parameters = list(inspect.signature(StopTaskCommand.execute).parameters)
        self.assertEqual(parameters, ["self", "task_id"])

    def test_derives_and_stores_run_summary_from_events_and_notes(self):
        # #626: the run narrative is machine-derived from the run's recorded
        # events + notes and stored on the run row, never asked of a human.
        client = _client()
        db = _tmp_db()
        created = db.create_run(1, "Bug", 10, "Project A")
        db.append_note(1, "Implementation plan: fix the rounding")
        _add_agent_event(db, task_id="1", subject="task_note")
        _add_agent_event(db, task_id="1", subject="task_note")
        with patch(_STOP_GUARD):
            result = _cmd_with_db(StopTaskCommand, client, db).execute(1)
        self.assertIn("task_note x2", result["run_summary"])
        self.assertIn("Implementation plan: fix the rounding", result["run_summary"])
        stored = db.get_run_by_id(created.id)
        self.assertEqual(stored.run_summary, result["run_summary"])

    def test_run_summary_none_when_nothing_recorded(self):
        # No events and no notes -> nothing to tell; the row stays NULL so the
        # billing upload applies its own fallback name.
        client = _client()
        db = _tmp_db()
        created = db.create_run(1, "Bug", 10, "Project A")
        with patch(_STOP_GUARD):
            result = _cmd_with_db(StopTaskCommand, client, db).execute(1)
        self.assertIsNone(result["run_summary"])
        self.assertIsNone(db.get_run_by_id(created.id).run_summary)

    def test_run_summary_has_no_length_cap(self):
        # Length policy (#626): derived summaries are internal/local text and
        # are NOT subject to the 300-char chatter cap.
        from odoo_sdk.commands.command import MAX_CHATTER_BODY_CHARS

        client = _client()
        db = _tmp_db()
        created = db.create_run(1, "Bug", 10, "Project A")
        long_note = "checkpoint " * 60  # well over 300 chars on its own
        db.append_note(1, long_note)
        with patch(_STOP_GUARD):
            result = _cmd_with_db(StopTaskCommand, client, db).execute(1)
        self.assertGreater(len(result["run_summary"]), MAX_CHATTER_BODY_CHARS)
        stored = db.get_run_by_id(created.id)
        self.assertEqual(stored.run_summary, result["run_summary"])

    def test_summary_scoped_to_the_run_window(self):
        # An event recorded BEFORE the run started (a previous run's work) must
        # not leak into this run's narrative.
        from datetime import datetime, timezone

        client = _client()
        db = _tmp_db()
        _add_agent_event(
            db,
            task_id="1",
            subject="old_tool",
            timestamp=datetime(2020, 1, 1, tzinfo=timezone.utc),
        )
        db.create_run(1, "Bug", 10, "Project A")
        _add_agent_event(db, task_id="1", subject="task_note")
        with patch(_STOP_GUARD):
            result = _cmd_with_db(StopTaskCommand, client, db).execute(1)
        self.assertIn("task_note", result["run_summary"])
        self.assertNotIn("old_tool", result["run_summary"])

    def test_raises_when_no_active_session(self):
        db = _tmp_db()
        with patch(_STOP_GUARD):
            with self.assertRaises(TaskNotRunningError):
                _cmd_with_db(StopTaskCommand, _client(), db).execute(999)

    def test_stop_from_awaiting_answers(self):
        client = _client()
        db = _tmp_db()
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=50)
        db.transition_to_awaiting(1)
        with patch(_STOP_GUARD):
            result = _cmd_with_db(StopTaskCommand, client, db).execute(1)
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
            _cmd_with_db(StopTaskCommand, client, db).execute(1)
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
        with patch(_START_GUARD):
            _cmd_with_db(StartTaskCommand, client, db).execute(
                task_id=10, task_name="Fix", project_id=5, project_name="Acct"
            )
        self._assert_no_agent_event(db)

    def test_stop_task_emits_no_agent_event(self):
        client = _client()
        db = _tmp_db()
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=50)
        with patch(_STOP_GUARD):
            _cmd_with_db(StopTaskCommand, client, db).execute(1)
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
        with patch(_RESUME_GUARD):
            _cmd_with_db(ResumeTaskCommand, client, db).execute(1)
        self._assert_no_agent_event(db)


if __name__ == "__main__":
    unittest.main()
