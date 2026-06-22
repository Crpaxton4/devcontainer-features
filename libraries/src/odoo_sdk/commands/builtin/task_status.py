from typing import Any

from ..command import Command
from odoo_sdk.task_tracker.env_check import assert_odoo_devcontainer
from odoo_sdk.task_tracker.state import TaskStateDB


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
        sessions = db.get_all_active_sessions()
        return [
            {
                "session_id": s.id,
                "task_id": s.task_id,
                "task_name": s.task_name,
                "project_name": s.project_name,
                "state": s.state.value,
                "started_at": s.started_at.isoformat(),
                "elapsed": s.elapsed_human,
            }
            for s in sessions
        ]
