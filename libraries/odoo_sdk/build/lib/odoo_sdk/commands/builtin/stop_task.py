from typing import Any

from ..command import Command, require_active_run
from ._registration import builtin_command
from odoo_sdk.utilities.env import assert_odoo_devcontainer


def _finalize_description(description: str) -> str:
    """Ensure the timesheet description carries the ``[/]`` prefix exactly once."""
    return description if description.startswith("[/]") else f"[/] {description}"


@builtin_command
class StopTaskCommand(Command):
    """Stop a task tracking session and finalize the local session state.

    Atomic and surface-agnostic: it takes the confirmed description directly (the
    MCP tool performs any review/edit elicitation) and never references MCP.

    This command does **not** write hours to the Odoo timesheet. The 0-hour
    ``[/] Work in progress`` anchor created at ``start_task`` is left untouched;
    the elapsed hours and final description are written to Odoo later by the
    TUI/ETL upload path (which owns all ``account.analytic.line`` hour writes).
    Stop only transitions the run to STOPPED and records the local session data.
    """

    _name = "stop_task"
    _description = (
        "Stop an active task tracking session. Transitions the run to stopped and "
        "records the confirmed work description (prefixed with [/]) locally. Does "
        "not write hours to Odoo — the TUI/ETL upload path owns timesheet hours."
    )

    def execute(
        self,
        task_id: int,
        description: str,
    ) -> dict[str, Any]:
        """Stop the active session for a task and record it locally.

        Does not write hours to Odoo; ``elapsed_hours`` is computed and returned
        for callers/tests to display, but the timesheet hour write is owned by
        the TUI/ETL upload path.

        :param task_id: Odoo project.task record id.
        :param description: Confirmed work summary recorded for the session.
        :return: Summary with task name, elapsed time, and confirmed description.
        """
        assert_odoo_devcontainer()
        db = self.state
        run = require_active_run(db, task_id)

        final_description = _finalize_description(description)
        elapsed_hours = run.elapsed_hours

        stopped = db.stop_run(task_id)

        return {
            "run_id": stopped.id,
            "task_name": stopped.task_name,
            "project_name": stopped.project_name,
            "elapsed": stopped.elapsed_human,
            "elapsed_hours": round(elapsed_hours, 4),
            "description": final_description,
            "timesheet_id": stopped.timesheet_id,
        }
