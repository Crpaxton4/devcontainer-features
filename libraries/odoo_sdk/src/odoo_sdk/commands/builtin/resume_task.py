from datetime import datetime, timezone
from typing import Any

from ..command import Command
from odoo_sdk.utilities.env import assert_odoo_devcontainer
from odoo_sdk.utilities.odoo_helpers import post_chatter_note
from odoo_sdk.utilities.timesheet import emit_agent_event
from odoo_sdk.state import LocalStateClient as TaskStateDB


class ResumeTaskCommand(Command):
    """Resume a task session that is AWAITING_ANSWERS, transitioning it back to RUNNING."""

    _name = "resume_task"
    _description = (
        "Resume an AWAITING_ANSWERS task session after receiving stakeholder answers. "
        "Posts a chatter note and transitions the session back to RUNNING."
    )

    def execute(self, task_id: int) -> dict[str, Any]:
        """Transition task from AWAITING_ANSWERS to RUNNING.

        :param task_id: Odoo project.task record id.
        :return: Confirmation with task name and resumed_at timestamp.
        """
        assert_odoo_devcontainer()
        db = TaskStateDB()
        session = db.transition_to_running(task_id)
        post_chatter_note(
            self._client,
            task_id,
            "Resuming implementation with received answers.",
        )
        emit_agent_event(db, task_id, f"resume_task: {session.task_name}")
        resumed_at = datetime.now(timezone.utc).isoformat()
        return {
            "task_name": session.task_name,
            "project_name": session.project_name,
            "state": session.state.value,
            "resumed_at": resumed_at,
        }
