from typing import Any, Dict, Optional

from ..command import Command
from ._registration import builtin_command


@builtin_command
class GetTodoCommand(Command):
    """Fetch a single project task by id."""

    _name = "get_todo"
    _description = "Returns one project task by id, or None if not found."

    def execute(self, task_id: int) -> Optional[Dict[str, Any]]:
        """Return the ``project.task`` with ``task_id``, or ``None``."""
        fields_to_fetch = [
            "name",
            "project_id",
            "stage_id",
            "user_ids",
            "date_deadline",
        ]
        records = (
            self._client["project.task"]
            .search([("id", "=", task_id)], limit=1)
            .read(fields_to_fetch)
        )
        return records[0] if records else None
