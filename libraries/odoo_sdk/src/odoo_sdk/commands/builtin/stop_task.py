from typing import Any

from ..command import Command
from odoo_sdk.state import TaskNotRunningError
from odoo_sdk.utilities.env import assert_odoo_devcontainer
from odoo_sdk.utilities.odoo_helpers import update_timesheet


def _finalize_description(description: str) -> str:
    """Ensure the timesheet description carries the ``[/]`` prefix exactly once."""
    return description if description.startswith("[/]") else f"[/] {description}"


class StopTaskCommand(Command):
    """Stop a task tracking session, finalize elapsed time, and update the timesheet.

    Atomic and surface-agnostic: it takes the confirmed description directly (the
    MCP tool performs any review/edit elicitation) and never references MCP.
    """

    _name = "stop_task"
    _description = (
        "Stop an active task tracking session. Logs the confirmed work description "
        "(prefixed with [/]) and updates the Odoo timesheet with elapsed hours."
    )

    def execute(
        self,
        task_id: int,
        description: str,
    ) -> dict[str, Any]:
        """Stop the active session for a task and finalize the timesheet.

        :param task_id: Odoo project.task record id.
        :param description: Confirmed work summary for the timesheet entry.
        :return: Summary with task name, elapsed time, and confirmed description.
        """
        assert_odoo_devcontainer()
        db = self.state
        session = db.get_active_session(task_id)
        if session is None:
            raise TaskNotRunningError(f"No active session for task {task_id}.")

        final_description = _finalize_description(description)
        elapsed_hours = session.elapsed_hours

        if session.timesheet_id is not None:
            update_timesheet(
                self._client,
                session.timesheet_id,
                elapsed_hours,
                final_description,
            )

        stopped = db.stop_session(task_id)

        return {
            "session_id": stopped.id,
            "task_name": stopped.task_name,
            "project_name": stopped.project_name,
            "elapsed": stopped.elapsed_human,
            "elapsed_hours": round(elapsed_hours, 4),
            "description": final_description,
            "timesheet_id": stopped.timesheet_id,
        }
