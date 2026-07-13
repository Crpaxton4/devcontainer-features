from typing import Any

from ..command import Command
from ._registration import builtin_command
from odoo_sdk.utilities.odoo_helpers import name_search_tasks


@builtin_command
class SearchTasksCommand(Command):
    """Search tasks within a project by name, returning candidate id/name dicts.

    Atomic building block for task selection. Disambiguation between multiple
    candidates is performed by the interaction surface, not by this command.
    """

    _name = "search_tasks"
    _description = (
        "Search Odoo project.task records by name substring within a project. "
        "Returns a list of {id, name} candidate dicts for disambiguation."
    )

    def execute(
        self, query: str, project_id: int, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Return tasks in ``project_id`` whose name matches ``query``.

        :param query: Task name substring to search for.
        :param project_id: Odoo project id to scope the search to.
        :param limit: Maximum number of candidates to return.
        :return: List of ``{"id", "name"}`` task candidate dicts.
        """
        return name_search_tasks(self._client, query, project_id, limit=limit)
