from typing import Any

from ..command import Command
from ._registration import builtin_command
from odoo_sdk.utilities.env import assert_odoo_devcontainer
from odoo_sdk.state import LocalStateClient as TaskStateDB


@builtin_command
class TaskStatusCommand(Command):
    """Return all active task tracking sessions with elapsed time."""

    _name = "task_status"
    _description = (
        "Show all actively tracked tasks (RUNNING or AWAITING_ANSWERS) "
        "with elapsed time for this project's git repository."
    )

    def execute(self) -> list[dict[str, Any]]:
        """Return active sessions with computed elapsed time.

        :return: List of active session dicts.
        """
        assert_odoo_devcontainer()
        db = TaskStateDB()
        runs = db.get_all_active_runs()
        return [
            {
                "run_id": s.id,
                "task_id": s.task_id,
                "task_name": s.task_name,
                "project_name": s.project_name,
                "state": s.state.value,
                "started_at": s.started_at.isoformat(),
                "elapsed": s.elapsed_human,
            }
            for s in runs
        ]
