from typing import Any, Optional

from ..command import Command
from ._registration import builtin_command
from odoo_sdk.utilities.env import assert_odoo_devcontainer
from odoo_sdk.utilities.odoo_helpers import name_search_projects


@builtin_command
class TaskListCommand(Command):
    """List project tasks assigned to the current user."""

    _name = "task_list"
    _description = (
        "List project.task records assigned to the authenticated user, "
        "optionally filtered by project name substring and/or stage name."
    )

    def execute(
        self,
        project_name_query: Optional[str] = None,
        stage: Optional[str] = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Search tasks assigned to the current user.

        :param project_name_query: Fuzzy project name to filter by.
        :param stage: Stage name substring to filter by.
        :param limit: Maximum number of tasks to return.
        :return: List of matching task records.
        """
        assert_odoo_devcontainer()
        uid = self._client.uid
        domain: list[Any] = [("user_ids", "in", [uid])]

        if project_name_query:
            projects = name_search_projects(self._client, project_name_query, limit=5)
            if not projects:
                return []
            project_ids = [p["id"] for p in projects]
            domain.append(("project_id", "in", project_ids))

        if stage:
            domain.append(("stage_id.name", "ilike", stage))

        return self._client.execute(
            "project.task",
            "search_read",
            domain,
            fields=["id", "name", "project_id", "stage_id", "date_deadline", "user_ids"],
            limit=limit,
        )
