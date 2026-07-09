from typing import Any

from ..command import Command
from odoo_sdk.utilities.odoo_helpers import name_search_projects


class SearchProjectsCommand(Command):
    """Search Odoo projects by name, returning candidate id/name dicts.

    Atomic building block for task selection. The interaction surface (MCP tool)
    disambiguates between multiple candidates via elicitation; this command never
    prompts and never references MCP.
    """

    _name = "search_projects"
    _description = (
        "Search Odoo project.project records by name substring. "
        "Returns a list of {id, name} candidate dicts for disambiguation."
    )

    def execute(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Return projects whose name matches ``query``.

        :param query: Project name substring to search for.
        :param limit: Maximum number of candidates to return.
        :return: List of ``{"id", "name"}`` project candidate dicts.
        """
        return name_search_projects(self._client, query, limit=limit)
