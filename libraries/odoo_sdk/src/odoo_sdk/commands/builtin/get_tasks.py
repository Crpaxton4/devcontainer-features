from typing import Any, Dict, List, Optional, Tuple

from ..command import Command
from ._registration import builtin_command


@builtin_command
class GetTasksCommand(Command):
    """List project tasks, optionally narrowed by an Odoo domain filter."""

    _name = "get_tasks"
    _description = "Lists project tasks with an optional domain filter."

    def execute(
        self,
        domain: Optional[List[Tuple[str, str, Any]]] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Search ``project.task`` and return summary fields for each match.

        :param domain: Optional Odoo domain filter; an empty domain matches all.
        :type domain: Optional[List[Tuple[str, str, Any]]]
        :param limit: Maximum number of tasks to return.
        :type limit: int
        :return: A list of task records with summary fields.
        :rtype: List[Dict[str, Any]]
        """

        task_domain = domain or []
        fields_to_fetch = [
            "name",
            "project_id",
            "stage_id",
            "user_ids",
            "date_deadline",
        ]
        return (
            self._client["project.task"]
            .search(task_domain, limit=limit)
            .read(fields_to_fetch)
        )
