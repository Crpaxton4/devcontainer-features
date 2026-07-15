"""Caller-level regression tests for #165 (keyword-only ``message_post``).

Issue #131 was that ``post_chatter_note`` forwarded the message options as a
trailing *positional* dict, which Odoo's keyword-only ``message_post`` rejects
with ``TypeError``. That helper is already fixed (options are forwarded as
``execute`` keyword arguments) and a helper-level regression lives in
``tests/test_utilities/test_odoo_helpers.py``
(``test_drives_keyword_only_message_post_without_type_error``).

These tests are *complementary*: instead of calling the helper directly, they
drive each chatter-posting command (``start_task``, ``resume_task``,
``task_note``, ``task_question``) end-to-end against a keyword-only
``message_post`` fake executor. The real (un-patched) ``post_chatter_note`` runs
inside each command, so a regression to a positional options dict would surface
here as a ``TypeError`` raised from the fake executor — proving every caller,
not just the helper in isolation, drives ``message_post`` with keyword options.
"""

import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from odoo_sdk.client import OdooClient
from odoo_sdk.commands.builtin.resume_task import ResumeTaskCommand
from odoo_sdk.commands.builtin.start_task import StartTaskCommand
from odoo_sdk.commands.builtin.task_note import TaskNoteCommand
from odoo_sdk.commands.builtin.task_question import TaskQuestionCommand
from odoo_sdk.state import LocalStateClient as TaskStateDB
from odoo_sdk.transport.executor import OdooExecutor
from tests.support import make_state_db

_NOTE_GUARD = "odoo_sdk.commands.builtin.task_note.assert_odoo_devcontainer"
_QUESTION_GUARD = "odoo_sdk.commands.builtin.task_question.assert_odoo_devcontainer"
_RESUME_GUARD = "odoo_sdk.commands.builtin.resume_task.assert_odoo_devcontainer"
_START_GUARD = "odoo_sdk.commands.builtin.start_task.assert_odoo_devcontainer"


class _KeywordOnlyMessagePostExecutor(OdooExecutor):
    """Fake executor mimicking Odoo's positional-args / keyword-options split.

    ``execute`` forwards positional ``*args`` as the record arguments and
    ``**kwargs`` as the method options, exactly like ``execute_kw`` does. The
    stubbed ``message_post`` is keyword-only, so any trailing positional options
    dict (the #131 bug) would reach ``_message_post`` as a second positional
    argument and raise ``TypeError`` — reproducing the server-side crash locally.
    Modelled on the executor of the same name in
    ``tests/test_utilities/test_odoo_helpers.py``.
    """

    def __init__(self) -> None:
        self.recorded: dict[str, Any] = {}
        # Real executors expose ``uid``; ``start_task`` reads ``client.uid`` when
        # resolving the employee id, so the fake must carry one too.
        self.uid = 7

    def execute(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        if (model, method) != ("project.task", "message_post"):
            raise AssertionError(f"unexpected call: {model}.{method}")
        return self._message_post(*args, **kwargs)

    def _message_post(
        self,
        ids: list[int],
        *,
        body: str = "",
        message_type: str = "notification",
        subtype_xmlid: str | None = None,
    ) -> int:
        self.recorded = {
            "ids": ids,
            "body": body,
            "message_type": message_type,
            "subtype_xmlid": subtype_xmlid,
        }
        return 777


def _tmp_db() -> TaskStateDB:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return make_state_db(Path(tmp.name))


def _keyword_client() -> tuple[OdooClient, _KeywordOnlyMessagePostExecutor]:
    executor = _KeywordOnlyMessagePostExecutor()
    return OdooClient(executor=executor), executor


class TestChatterCallersDriveKeywordOnlyMessagePost(unittest.TestCase):
    """Every chatter caller must post with keyword options, never positional."""

    def _assert_recorded_keyword(
        self,
        executor: _KeywordOnlyMessagePostExecutor,
        *,
        task_id: int,
        body: str,
    ) -> None:
        # Reaching this point already proves no ``TypeError`` was raised, i.e.
        # the options were forwarded as keywords (a positional options dict
        # would have crashed the keyword-only ``_message_post``).
        self.assertEqual(
            executor.recorded,
            {
                "ids": [task_id],
                "body": body,
                "message_type": "comment",
                "subtype_xmlid": "mail.mt_note",
            },
        )

    def test_start_task_drives_keyword_only_message_post(self):
        client, executor = _keyword_client()
        db = _tmp_db()
        with (
            patch(_START_GUARD),
            patch(
                "odoo_sdk.utilities.timesheet.get_employee_id",
                return_value=3,
            ),
            patch(
                "odoo_sdk.commands.builtin.start_task.ensure_anchor",
                return_value=99,
            ),
        ):
            StartTaskCommand(client, state=db).execute(
                task_id=10,
                task_name="Fix VAT",
                project_id=5,
                project_name="Accounting",
            )
        # Markdown is rendered to HTML before posting (issue #324).
        self._assert_recorded_keyword(
            executor, task_id=10, body="<p>Work started on this task.</p>"
        )

    def test_resume_task_drives_keyword_only_message_post(self):
        client, executor = _keyword_client()
        db = _tmp_db()
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=1)
        db.transition_to_awaiting(1)
        with (
            patch(_RESUME_GUARD),
            patch(
                "odoo_sdk.commands.builtin.resume_task.TaskStateDB",
                return_value=db,
            ),
        ):
            ResumeTaskCommand(client).execute(1)
        self._assert_recorded_keyword(
            executor,
            task_id=1,
            body="<p>Resuming implementation with received answers.</p>",
        )

    def test_task_note_drives_keyword_only_message_post(self):
        client, executor = _keyword_client()
        db = _tmp_db()
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=1)
        with (
            patch(_NOTE_GUARD),
            patch(
                "odoo_sdk.commands.builtin.task_note.TaskStateDB",
                return_value=db,
            ),
        ):
            TaskNoteCommand(client).execute(1, "Note text")
        self._assert_recorded_keyword(
            executor, task_id=1, body="<p>Note text</p>"
        )

    def test_task_question_drives_keyword_only_message_post(self):
        client, executor = _keyword_client()
        db = _tmp_db()
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=1)
        with (
            patch(_QUESTION_GUARD),
            patch(
                "odoo_sdk.commands.builtin.task_question.TaskStateDB",
                return_value=db,
            ),
        ):
            TaskQuestionCommand(client).execute(1, "Which approach?")
        self._assert_recorded_keyword(
            executor, task_id=1, body="<p>[?] Which approach?</p>"
        )


if __name__ == "__main__":
    unittest.main()
