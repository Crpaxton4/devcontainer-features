from datetime import datetime, timezone
from typing import Any

from ..command import Command
from ._registration import builtin_command
from odoo_sdk.utilities.env import assert_odoo_devcontainer
from odoo_sdk.utilities.odoo_helpers import post_chatter_note
from odoo_sdk.state import LocalStateClient as TaskStateDB


@builtin_command
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
        run = db.transition_to_running(task_id)
        post_chatter_note(
            self._client,
            task_id,
            "Resuming implementation with received answers.",
        )
        resumed_at = datetime.now(timezone.utc).isoformat()
        return {
            "task_name": run.task_name,
            "project_name": run.project_name,
            "state": run.state.value,
            "resumed_at": resumed_at,
        }
