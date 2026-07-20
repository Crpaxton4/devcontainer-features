from typing import Any, Optional

from ..command import Command, require_active_run
from ._registration import builtin_command
from odoo_sdk.utilities.env import assert_odoo_devcontainer


def _finalize_description(description: Optional[str]) -> Optional[str]:
    """Ensure the work description carries the ``[/]`` prefix exactly once.

    Returns ``None`` for a missing or blank description: the summary is optional
    (#482), so there is nothing to prefix.
    """
    if not description or not description.strip():
        return None
    return description if description.startswith("[/]") else f"[/] {description}"


@builtin_command
class StopTaskCommand(Command):
    """Stop a task tracking session and finalize the local session state.

    Atomic and surface-agnostic: it takes the confirmed description directly (the
    MCP tool performs any review/edit elicitation) and never references MCP.

    This command does **not** write hours to the Odoo timesheet, and ``start_task``
    creates no timesheet anchor to close out; the elapsed hours are written to
    Odoo later by the TUI/ETL upload path (which owns all
    ``account.analytic.line`` hour writes). Stop only transitions the run to
    STOPPED and records the local session data. The work ``description`` is
    therefore optional (#482) and is echoed back for display only — nothing
    downstream consumes it for time logging.
    """

    _name = "stop_task"
    _description = (
        "Stop an active task tracking session. Transitions the run to stopped and "
        "echoes back an optional work description (prefixed with [/]). Does not "
        "write hours to Odoo — the TUI/ETL upload path owns timesheet hours."
    )

    def execute(
        self,
        task_id: int,
        description: Optional[str] = None,
    ) -> dict[str, Any]:
        """Stop the active session for a task and record it locally.

        Does not write hours to Odoo; ``elapsed_hours`` is computed and returned
        for callers/tests to display, but the timesheet hour write is owned by
        the TUI/ETL upload path.

        :param task_id: Odoo project.task record id.
        :param description: Optional work summary echoed back for the session.
            Omitted or blank yields a ``None`` ``description`` in the result.
        :return: Summary with task name, elapsed time, and the description.
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
