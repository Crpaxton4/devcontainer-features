from typing import Any

from pydantic import BaseModel

from ..command import Command
from odoo_sdk.task_tracker.env_check import assert_odoo_devcontainer
from odoo_sdk.task_tracker.odoo_ops import update_timesheet
from odoo_sdk.task_tracker.state import TaskNotRunningError, TaskStateDB


class _ReviewDescription(BaseModel):
    description: str


class StopTaskCommand(Command):
    """Stop a task tracking session, finalize elapsed time, and update the Odoo timesheet."""

    _name = "stop_task"
    _description = (
        "Stop an active task tracking session. Presents the AI-generated work description "
        "for review before logging. Updates the Odoo timesheet with elapsed hours and "
        "confirmed description prefixed with [/]."
    )

    async def execute(
        self,
        task_id: int,
        description: str,
        ctx: Any,
    ) -> dict[str, Any]:
        """Stop the active session for a task and finalize the timesheet.

        :param task_id: Odoo project.task record id.
        :param description: AI-generated work summary for the timesheet entry.
        :param ctx: FastMCP Context for elicitation.
        :return: Summary with task name, elapsed time, and confirmed description.
        """
        assert_odoo_devcontainer()
        db = TaskStateDB()
        session = db.get_active_session(task_id)
        if session is None:
            raise TaskNotRunningError(f"No active session for task {task_id}.")

        # Present description for review/edit
        review = await ctx.elicit(
            "Review the timesheet description before logging:",
            _ReviewDescription,
        )
        if review.action != "accept":
            return {"error": "Stop task cancelled."}

        confirmed_description = review.data.description or description
        final_description = (
            confirmed_description
            if confirmed_description.startswith("[/]")
            else f"[/] {confirmed_description}"
        )

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
