from typing import Any

from ..command import Command
from odoo_sdk.state import TaskNotRunningError
from odoo_sdk.utilities.env import assert_odoo_devcontainer


class AbortTaskCommand(Command):
    """Force-close a wedged task session without writing any hours.

    Unlike ``stop_task``, this never logs elapsed time. It moves a stuck
    ``RUNNING`` / ``AWAITING_ANSWERS`` session straight to ``STOPPED``. The
    orphaned placeholder timesheet is intentionally left in place: the SDK never
    deletes records.
    """

    _name = "abort_task"
    _description = (
        "Force-close a wedged RUNNING or AWAITING_ANSWERS task session without "
        "logging hours. The placeholder timesheet entry is left untouched."
    )

    def execute(self, task_id: int) -> dict[str, Any]:
        """Force-close the active session for a task without logging hours.

        :param task_id: Odoo project.task record id.
        :return: Summary of the aborted session.
        :raises TaskNotRunningError: When there is no active session to abort.
        """
        assert_odoo_devcontainer()
        db = self.state
        run = db.get_active_run(task_id)
        if run is None:
            raise TaskNotRunningError(f"No active session for task {task_id}.")

        stopped = db.stop_run(task_id)

        return {
            "run_id": stopped.id,
            "task_name": stopped.task_name,
            "project_name": stopped.project_name,
            "elapsed": stopped.elapsed_human,
            "aborted": True,
            "timesheet_id": stopped.timesheet_id,
        }
