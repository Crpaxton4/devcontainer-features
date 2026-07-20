from datetime import datetime, timezone
from typing import Any

from ..command import Command
from ._registration import builtin_command
from odoo_sdk.utilities.env import assert_odoo_devcontainer


@builtin_command
class ResumeTaskCommand(Command):
    """Resume a paused task session back to RUNNING.

    Two predecessors resume (#504): an ``AWAITING_ANSWERS`` session (after
    stakeholder answers arrive) and a ``STOPPED`` session (work continues after a
    stop) — the stopped run is reopened in place, preserving its original start so
    one effort stays one run. The transition is the whole command: no chatter note
    is posted (#505). The former fixed ``"Resuming implementation with received
    answers."`` marker carried no information the event row does not already
    record, and its unguarded post could raise after the state had already moved
    to RUNNING.
    """

    _name = "resume_task"
    _description = (
        "Resume a paused task session back to RUNNING — either after receiving "
        "stakeholder answers (AWAITING_ANSWERS) or to continue a stopped session "
        "(STOPPED), which is reopened in place rather than started anew."
    )

    def execute(self, task_id: int) -> dict[str, Any]:
        """Transition a task from AWAITING_ANSWERS or STOPPED to RUNNING.

        :param task_id: Odoo project.task record id.
        :return: Confirmation with task name and resumed_at timestamp.
        """
        assert_odoo_devcontainer()
        db = self.state
        run = db.transition_to_running(task_id)
        resumed_at = datetime.now(timezone.utc).isoformat()
        return {
            "task_name": run.task_name,
            "project_name": run.project_name,
            "state": run.state.value,
            "resumed_at": resumed_at,
        }
