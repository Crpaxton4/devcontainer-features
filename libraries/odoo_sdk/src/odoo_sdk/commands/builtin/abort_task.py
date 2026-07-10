from typing import Any

from ..command import Command
from odoo_sdk.utilities.env import assert_odoo_devcontainer


class AbortTaskCommand(Command):
    """Force-close a wedged task session without writing any hours.

    Unlike ``stop_task``, this never logs elapsed time. It moves a stuck
    ``RUNNING`` / ``AWAITING_ANSWERS`` session straight to ``STOPPED`` and
    unlinks the orphaned placeholder timesheet so no zero-hour
    ``account.analytic.line`` is left behind.
    """

    _name = "abort_task"
    _description = (
        "Force-close a wedged RUNNING or AWAITING_ANSWERS task session without "
        "logging hours, and delete its placeholder timesheet entry."
    )

    def execute(self, task_id: int) -> dict[str, Any]:
        """Force-close the active session for a task, discarding its timesheet.

        :param task_id: Odoo project.task record id.
        :return: Summary of the aborted session, or an ``error`` dict when there
            is no active session to abort.
        """
        assert_odoo_devcontainer()
        db = self.state
        session = db.get_active_session(task_id)
        if session is None:
            return {"error": f"No active session for task {task_id}."}

        timesheet_id = session.timesheet_id
        stopped = db.stop_session(task_id)

        if timesheet_id is not None:
            self._client.execute(
                "account.analytic.line",
                "unlink",
                [timesheet_id],
            )

        return {
            "session_id": stopped.id,
            "task_name": stopped.task_name,
            "project_name": stopped.project_name,
            "elapsed": stopped.elapsed_human,
            "aborted": True,
            "timesheet_id": timesheet_id,
        }
