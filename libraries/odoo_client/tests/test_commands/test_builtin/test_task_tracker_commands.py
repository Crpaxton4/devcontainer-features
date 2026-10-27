"""Tests for task-tracking Command subclasses."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from odoo_client.commands.builtin.get_task import GetTaskCommand
from odoo_client.commands.builtin.get_task_attachments import GetTaskAttachmentsCommand
from odoo_client.commands.builtin.get_task_chatter import GetTaskChatterCommand
from odoo_client.commands.builtin.search_projects import SearchProjectsCommand
from odoo_client.commands.builtin.search_tasks import SearchTasksCommand
from odoo_client.commands.builtin.task_list import TaskListCommand
from odoo_client.commands.builtin.task_note import TaskNoteCommand
from odoo_client.commands.builtin.task_question import TaskQuestionCommand
from odoo_client.commands.command import MAX_CHATTER_BODY_CHARS
from odoo_client.state import LocalStateClient as TaskStateDB
from tests.support import make_state_db

_NOTE = "odoo_client.commands.builtin.task_note"
_QUESTION = "odoo_client.commands.builtin.task_question"


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
            "odoo_client.commands.builtin.get_task_chatter.get_task_chatter",
            return_value=expected,
        ) as mock_chatter:
            result = GetTaskChatterCommand(client).execute(task_id=42)
        mock_chatter.assert_called_once_with(client, 42, limit=100, since=None)
        self.assertEqual(result, expected)

    def test_passes_custom_limit(self):
        client = _client()
        with patch(
            "odoo_client.commands.builtin.get_task_chatter.get_task_chatter",
            return_value=[],
        ) as mock_chatter:
            GetTaskChatterCommand(client).execute(task_id=10, limit=5)
        mock_chatter.assert_called_once_with(client, 10, limit=5, since=None)

    def test_passes_since_cursor(self):
        # #624: both cursor forms ride through to the helper untouched.
        client = _client()
        with patch(
            "odoo_client.commands.builtin.get_task_chatter.get_task_chatter",
            return_value=[],
        ) as mock_chatter:
            GetTaskChatterCommand(client).execute(task_id=10, since=77)
            GetTaskChatterCommand(client).execute(
                task_id=10, since="2026-06-20 10:30:00"
            )
        mock_chatter.assert_any_call(client, 10, limit=100, since=77)
        mock_chatter.assert_any_call(client, 10, limit=100, since="2026-06-20 10:30:00")


# ── GetTaskAttachmentsCommand ─────────────────────────────────────────────────


class TestGetTaskAttachmentsCommand(unittest.TestCase):
    def test_delegates_to_helper(self):
        client = _client()
        expected = [{"id": 1, "name": "file.png", "source": "task"}]
        with patch(
            "odoo_client.commands.builtin.get_task_attachments.get_task_attachments",
            return_value=expected,
        ) as mock_helper:
            result = GetTaskAttachmentsCommand(client).execute(task_id=42)
        mock_helper.assert_called_once_with(client, 42, include_content=False)
        self.assertEqual(result, expected)

    def test_passes_include_content(self):
        client = _client()
        with patch(
            "odoo_client.commands.builtin.get_task_attachments.get_task_attachments",
            return_value=[],
        ) as mock_helper:
            GetTaskAttachmentsCommand(client).execute(task_id=10, include_content=True)
        mock_helper.assert_called_once_with(client, 10, include_content=True)


# ── GetTaskCommand ────────────────────────────────────────────────────────────


class TestGetTaskCommand(unittest.TestCase):
    def test_returns_none_when_task_not_found(self):
        client = _client()
        with (
            patch(
                "odoo_client.commands.builtin.get_task.get_task_detail",
                return_value=None,
            ),
            patch(
                "odoo_client.commands.builtin.get_task.get_task_chatter"
            ) as mock_chatter,
        ):
            result = GetTaskCommand(client).execute(task_id=999)
        self.assertIsNone(result)
        mock_chatter.assert_not_called()

    def test_merges_chatter_when_requested(self):
        client = _client()
        task_data = {"task_id": 42, "name": "Feature X", "description": "Do it"}
        chatter_data = [{"id": 1, "author": "Jane", "body": "Note"}]
        with (
            patch(
                "odoo_client.commands.builtin.get_task.get_task_detail",
                return_value=task_data,
            ),
            patch(
                "odoo_client.commands.builtin.get_task.get_task_chatter",
                return_value=chatter_data,
            ),
        ):
            result = GetTaskCommand(client).execute(task_id=42, include=["chatter"])
        self.assertEqual(result["chatter"], chatter_data)
        self.assertEqual(result["name"], "Feature X")

    def test_default_does_not_fetch_chatter(self):
        client = _client()
        task_data = {"task_id": 42, "name": "Feature X", "description": "Do it"}
        with (
            patch(
                "odoo_client.commands.builtin.get_task.get_task_detail",
                return_value=task_data,
            ) as mock_detail,
            patch(
                "odoo_client.commands.builtin.get_task.get_task_chatter"
            ) as mock_chatter,
        ):
            result = GetTaskCommand(client).execute(task_id=42)
        self.assertNotIn("chatter", result)
        mock_chatter.assert_not_called()
        mock_detail.assert_called_once_with(client, 42, include=None)

    def test_forwards_include_to_get_task_detail(self):
        client = _client()
        with (
            patch(
                "odoo_client.commands.builtin.get_task.get_task_detail",
                return_value={"task_id": 7, "name": "T"},
            ) as mock_detail,
            patch(
                "odoo_client.commands.builtin.get_task.get_task_chatter",
                return_value=[],
            ) as mock_chatter,
        ):
            GetTaskCommand(client).execute(task_id=7, include=["subtasks", "chatter"])
        mock_detail.assert_called_once_with(client, 7, include=["subtasks", "chatter"])
        mock_chatter.assert_called_once_with(client, 7)


# ── TaskListCommand ───────────────────────────────────────────────────────────


class TestTaskListCommand(unittest.TestCase):
    def test_searches_without_filters(self):
        client = _client()
        client.execute.return_value = [{"id": 1, "name": "Bug fix"}]
        result = TaskListCommand(client).execute()
        client.execute.assert_called_once()
        call = client.execute.call_args
        domain = call.args[2]
        self.assertIn(("user_ids", "in", [7]), domain)
        self.assertEqual(result, [{"id": 1, "name": "Bug fix"}])

    def test_applies_stage_filter(self):
        client = _client()
        client.execute.return_value = []
        TaskListCommand(client).execute(stage="Done")
        call = client.execute.call_args
        domain = call.args[2]
        self.assertIn(("stage_id.name", "ilike", "Done"), domain)

    def test_returns_empty_when_no_project_match(self):
        client = _client()
        with (
            patch(
                "odoo_client.commands.builtin.task_list.name_search_projects",
                return_value=[],
            ),
        ):
            result = TaskListCommand(client).execute(project_name_query="xyz")
        self.assertEqual(result, [])

    def test_adds_project_id_filter_when_found(self):
        client = _client()
        client.execute.return_value = []
        with (
            patch(
                "odoo_client.commands.builtin.task_list.name_search_projects",
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
        TaskListCommand(client).execute(limit=5)
        call = client.execute.call_args
        self.assertEqual(call.kwargs["limit"], 5)


def _detail(name="Bug"):
    """A ``get_task_detail`` result naming the task, for the chatter commands."""
    return patch(f"{_NOTE}.get_task_detail", return_value={"name": name})


def _q_detail(name="Bug"):
    return patch(f"{_QUESTION}.get_task_detail", return_value={"name": name})


# ── TaskNoteCommand ───────────────────────────────────────────────────────────


class TestTaskNoteCommand(unittest.TestCase):
    def test_posts_note_without_any_run(self):
        # decision 10: the job row is the only run record, so the note needs
        # no active run — it resolves the task and posts to the chatter.
        client = _client()
        db = _tmp_db()
        with (
            _detail("Bug"),
            patch(f"{_NOTE}.post_chatter_note", return_value=55) as mock_post,
        ):
            result = _cmd_with_db(TaskNoteCommand, client, db).execute(1, "Note text")
        mock_post.assert_called_once_with(client, 1, "Note text", attachment_ids=None)
        self.assertEqual(
            result, {"task_name": "Bug", "message_id": 55, "note": "Note text"}
        )

    def test_unknown_task_raises_and_posts_nothing(self):
        client = _client()
        db = _tmp_db()
        with (
            patch(f"{_NOTE}.get_task_detail", return_value=None),
            patch(f"{_NOTE}.post_chatter_note") as mock_post,
        ):
            with self.assertRaises(ValueError) as ctx:
                _cmd_with_db(TaskNoteCommand, client, db).execute(404, "Note")
        self.assertIn("404", str(ctx.exception))
        mock_post.assert_not_called()

    def test_writes_nothing_to_the_local_store(self):
        # No run write, no event: the command only posts and records the key.
        client = _client()
        db = _tmp_db()
        with _detail(), patch(f"{_NOTE}.post_chatter_note", return_value=1):
            _cmd_with_db(TaskNoteCommand, client, db).execute(1, "progress note")
        self.assertEqual(db.get_events(), [])

    def test_attachments_created_and_linked_to_post(self):
        # #604: file specs become ir.attachment records linked to the task,
        # and their ids ride to ``post_chatter_note`` / the result payload.
        client = _client()
        db = _tmp_db()
        specs = [{"content": "aGk=", "name": "a.md"}, {"path": "/tmp/b.csv"}]
        with (
            _detail(),
            patch(f"{_NOTE}.create_attachments", return_value=[91, 92]) as mock_create,
            patch(f"{_NOTE}.post_chatter_note", return_value=55) as mock_post,
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

    def test_rejects_note_over_the_chatter_cap(self):
        # #610: an over-limit note is rejected — nothing is uploaded or posted.
        over = "x" * (MAX_CHATTER_BODY_CHARS + 1)
        client = _client()
        db = _tmp_db()
        with _detail(), patch(f"{_NOTE}.post_chatter_note") as mock_post:
            with self.assertRaises(ValueError) as ctx:
                _cmd_with_db(TaskNoteCommand, client, db).execute(1, over)
        message = str(ctx.exception)
        self.assertIn(str(len(over)), message)
        self.assertIn("500", message)
        self.assertIn("simple, direct, plain", message)
        mock_post.assert_not_called()

    def test_accepts_note_of_exactly_the_chatter_cap(self):
        client = _client()
        db = _tmp_db()
        note = "y" * MAX_CHATTER_BODY_CHARS
        with _detail(), patch(f"{_NOTE}.post_chatter_note", return_value=56) as post:
            result = _cmd_with_db(TaskNoteCommand, client, db).execute(1, note)
        post.assert_called_once_with(client, 1, note, attachment_ids=None)
        self.assertEqual(result["message_id"], 56)

    def test_cap_is_500_since_901(self):
        # #901: 300 chars split one coherent finding across several posted
        # messages, which was itself a driver of the note volume.
        self.assertEqual(MAX_CHATTER_BODY_CHARS, 500)

    def test_description_advertises_the_500_char_limit(self):
        # The limit must be visible to MCP callers up front: the command
        # description (which becomes the tool description) names it.
        self.assertIn("500", TaskNoteCommand._description)

    def test_description_states_attachment_audience(self):
        description = TaskNoteCommand._description
        self.assertIn("CLIENT-VISIBLE", description)
        self.assertIn("deliverables the client asked", description)

    def test_description_has_no_run_vocabulary(self):
        description = TaskNoteCommand._description
        self.assertNotIn("interim", description)
        self.assertNotIn("tracking session", description)

    def test_dedupe_miss_posts_and_records_key(self):
        # #631: a fresh key behaves like a normal post, then maps the key to
        # the message so a later retry can find it.
        client = _client()
        db = _tmp_db()
        with _detail(), patch(f"{_NOTE}.post_chatter_note", return_value=55) as post:
            result = _cmd_with_db(TaskNoteCommand, client, db).execute(
                1, "Once only", dedupe_key="note-abc"
            )
        post.assert_called_once()
        self.assertEqual(result["message_id"], 55)
        self.assertFalse(result["deduplicated"])
        self.assertEqual(db.get_chatter_dedupe(1, "note-abc"), 55)

    def test_dedupe_hit_skips_all_side_effects(self):
        # #631 acceptance: the second call with the same key posts nothing and
        # uploads nothing — it returns the first message id.
        client = _client()
        db = _tmp_db()
        db.record_chatter_dedupe(1, "note-abc", 55)
        with (
            _detail("Bug"),
            patch(f"{_NOTE}.create_attachments") as mock_create,
            patch(f"{_NOTE}.post_chatter_note") as mock_post,
        ):
            result = _cmd_with_db(TaskNoteCommand, client, db).execute(
                1,
                "Once only",
                attachments=[{"path": "/tmp/a.md"}],
                dedupe_key="note-abc",
            )
        mock_post.assert_not_called()
        mock_create.assert_not_called()
        self.assertEqual(result["message_id"], 55)
        self.assertTrue(result["deduplicated"])
        self.assertEqual(result["task_name"], "Bug")

    def test_dedupe_keys_are_scoped_per_task(self):
        # The same key on a DIFFERENT task is a miss: task 2 never sees task
        # 1's message.
        client = _client()
        db = _tmp_db()
        db.record_chatter_dedupe(1, "shared-key", 55)
        with _detail(), patch(f"{_NOTE}.post_chatter_note", return_value=66) as post:
            result = _cmd_with_db(TaskNoteCommand, client, db).execute(
                2, "Other task", dedupe_key="shared-key"
            )
        post.assert_called_once()
        self.assertEqual(result["message_id"], 66)

    def test_omitted_dedupe_key_keeps_contract_unchanged(self):
        # #631: without a key the response carries no 'deduplicated' field and
        # repeated calls post repeatedly (the pre-#631 behavior).
        client = _client()
        db = _tmp_db()
        with _detail(), patch(f"{_NOTE}.post_chatter_note", return_value=55) as post:
            first = _cmd_with_db(TaskNoteCommand, client, db).execute(1, "Twice")
            second = _cmd_with_db(TaskNoteCommand, client, db).execute(1, "Twice")
        self.assertEqual(post.call_count, 2)
        self.assertNotIn("deduplicated", first)
        self.assertNotIn("deduplicated", second)

    def test_failed_post_leaves_key_unclaimed(self):
        # The key maps only AFTER a successful post, so a failed post stays
        # retryable under the same key.
        client = _client()
        db = _tmp_db()
        with (
            _detail(),
            patch(f"{_NOTE}.post_chatter_note", side_effect=RuntimeError("odoo down")),
        ):
            with self.assertRaises(RuntimeError):
                _cmd_with_db(TaskNoteCommand, client, db).execute(
                    1, "Kept note", dedupe_key="retry-me"
                )
        self.assertIsNone(db.get_chatter_dedupe(1, "retry-me"))


# ── TaskQuestionCommand ───────────────────────────────────────────────────────


class TestTaskQuestionCommand(unittest.TestCase):
    def test_posts_prefixed_question_without_any_run(self):
        client = _client()
        db = _tmp_db()
        with (
            _q_detail("Bug"),
            patch(f"{_QUESTION}.post_chatter_note", return_value=77) as mock_post,
        ):
            result = _cmd_with_db(TaskQuestionCommand, client, db).execute(
                1, "Which approach?"
            )
        mock_post.assert_called_once_with(client, 1, "[?] Which approach?")
        self.assertEqual(
            result,
            {"task_name": "Bug", "message_id": 77, "question": "Which approach?"},
        )

    def test_unknown_task_raises_and_posts_nothing(self):
        client = _client()
        db = _tmp_db()
        with (
            patch(f"{_QUESTION}.get_task_detail", return_value=None),
            patch(f"{_QUESTION}.post_chatter_note") as mock_post,
        ):
            with self.assertRaises(ValueError):
                _cmd_with_db(TaskQuestionCommand, client, db).execute(404, "Q?")
        mock_post.assert_not_called()

    def test_writes_nothing_to_the_local_store(self):
        client = _client()
        db = _tmp_db()
        with _q_detail(), patch(f"{_QUESTION}.post_chatter_note", return_value=1):
            _cmd_with_db(TaskQuestionCommand, client, db).execute(1, "which?")
        self.assertEqual(db.get_events(), [])

    def test_rejects_question_over_the_chatter_cap(self):
        over = "x" * (MAX_CHATTER_BODY_CHARS + 1)
        client = _client()
        db = _tmp_db()
        with _q_detail(), patch(f"{_QUESTION}.post_chatter_note") as mock_post:
            with self.assertRaises(ValueError) as ctx:
                _cmd_with_db(TaskQuestionCommand, client, db).execute(1, over)
        self.assertIn("500", str(ctx.exception))
        mock_post.assert_not_called()

    def test_accepts_question_of_exactly_the_chatter_cap(self):
        client = _client()
        db = _tmp_db()
        question = "q" * MAX_CHATTER_BODY_CHARS
        with _q_detail(), patch(f"{_QUESTION}.post_chatter_note", return_value=9):
            result = _cmd_with_db(TaskQuestionCommand, client, db).execute(1, question)
        self.assertEqual(result["message_id"], 9)

    def test_description_advertises_the_500_char_limit(self):
        self.assertIn("500", TaskQuestionCommand._description)

    def test_description_has_no_state_machine_vocabulary(self):
        self.assertNotIn("AWAITING_ANSWERS", TaskQuestionCommand._description)
        self.assertNotIn("watermark", TaskQuestionCommand._description)

    def test_dedupe_miss_posts_and_records_key(self):
        client = _client()
        db = _tmp_db()
        with _q_detail(), patch(f"{_QUESTION}.post_chatter_note", return_value=88):
            result = _cmd_with_db(TaskQuestionCommand, client, db).execute(
                1, "Which?", dedupe_key="q-abc"
            )
        self.assertEqual(result["message_id"], 88)
        self.assertFalse(result["deduplicated"])
        self.assertEqual(db.get_chatter_dedupe(1, "q-abc"), 88)

    def test_dedupe_hit_skips_the_post(self):
        client = _client()
        db = _tmp_db()
        db.record_chatter_dedupe(1, "q-abc", 88)
        with _q_detail(), patch(f"{_QUESTION}.post_chatter_note") as mock_post:
            result = _cmd_with_db(TaskQuestionCommand, client, db).execute(
                1, "Which?", dedupe_key="q-abc"
            )
        mock_post.assert_not_called()
        self.assertEqual(result["message_id"], 88)
        self.assertTrue(result["deduplicated"])

    def test_omitted_dedupe_key_keeps_contract_unchanged(self):
        client = _client()
        db = _tmp_db()
        with _q_detail(), patch(f"{_QUESTION}.post_chatter_note", return_value=5):
            result = _cmd_with_db(TaskQuestionCommand, client, db).execute(1, "Q?")
        self.assertNotIn("deduplicated", result)


# ── SearchProjectsCommand ─────────────────────────────────────────────────────


class TestSearchProjectsCommand(unittest.TestCase):
    def test_delegates_to_name_search_projects(self):
        client = _client()
        with patch(
            "odoo_client.commands.builtin.search_projects.name_search_projects",
            return_value=[{"id": 5, "name": "Accounting"}],
        ) as mock_search:
            result = SearchProjectsCommand(client).execute("Acc")
        mock_search.assert_called_once_with(client, "Acc", limit=10)
        self.assertEqual(result, [{"id": 5, "name": "Accounting"}])

    def test_passes_custom_limit(self):
        client = _client()
        with patch(
            "odoo_client.commands.builtin.search_projects.name_search_projects",
            return_value=[],
        ) as mock_search:
            SearchProjectsCommand(client).execute("x", limit=3)
        mock_search.assert_called_once_with(client, "x", limit=3)

    def test_returns_empty_list_when_no_matches(self):
        client = _client()
        with patch(
            "odoo_client.commands.builtin.search_projects.name_search_projects",
            return_value=[],
        ):
            self.assertEqual(SearchProjectsCommand(client).execute("nope"), [])


# ── SearchTasksCommand ────────────────────────────────────────────────────────


class TestSearchTasksCommand(unittest.TestCase):
    def test_delegates_to_name_search_tasks_with_project_scope(self):
        client = _client()
        with patch(
            "odoo_client.commands.builtin.search_tasks.name_search_tasks",
            return_value=[{"id": 10, "name": "Fix VAT"}],
        ) as mock_search:
            result = SearchTasksCommand(client).execute("VAT", project_id=5)
        mock_search.assert_called_once_with(client, "VAT", 5, limit=10)
        self.assertEqual(result, [{"id": 10, "name": "Fix VAT"}])

    def test_passes_custom_limit(self):
        client = _client()
        with patch(
            "odoo_client.commands.builtin.search_tasks.name_search_tasks",
            return_value=[],
        ) as mock_search:
            SearchTasksCommand(client).execute("x", project_id=1, limit=2)
        mock_search.assert_called_once_with(client, "x", 1, limit=2)


if __name__ == "__main__":
    unittest.main()
