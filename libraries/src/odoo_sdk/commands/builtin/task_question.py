from typing import Any

from ..command import Command
from odoo_sdk.task_tracker.env_check import assert_odoo_devcontainer
from odoo_sdk.task_tracker.odoo_ops import post_chatter_note
from odoo_sdk.task_tracker.state import TaskNotRunningError, TaskState, TaskStateDB


class TaskQuestionCommand(Command):
    """Post a question to a task's chatter and transition to AWAITING_ANSWERS."""

    _name = "task_question"
    _description = (
        "Post a question (prefixed with [?]) to the Odoo task chatter. "
        "Transitions the session from RUNNING to AWAITING_ANSWERS. "
        "Multiple questions are allowed (self-loop on AWAITING_ANSWERS)."
    )

    def execute(self, task_id: int, question: str) -> dict[str, Any]:
        """Post a question and update session state.

        :param task_id: Odoo project.task record id.
        :param question: Question text to post.
        :return: Confirmation with message id and new state.
        """
        assert_odoo_devcontainer()
        db = TaskStateDB()
        session = db.get_active_session(task_id)
        if session is None:
            raise TaskNotRunningError(f"No active session for task {task_id}.")

        body = f"[?] {question}"
        message_id = post_chatter_note(self._client, task_id, body)

        if session.state == TaskState.RUNNING:
            session = db.transition_to_awaiting(task_id)

        return {
            "task_name": session.task_name,
            "message_id": message_id,
            "question": question,
            "state": session.state.value,
        }
