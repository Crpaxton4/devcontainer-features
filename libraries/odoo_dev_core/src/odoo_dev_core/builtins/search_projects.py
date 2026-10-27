"""Search Odoo projects by name, returning candidate id/name dicts.

Atomic building block for task selection. The interaction surface (MCP tool)
disambiguates between multiple candidates via elicitation; this command never
prompts and never references MCP."""

from dataclasses import dataclass
from typing import Any
from odoo_dev_core.services.odoo_helpers import name_search_projects

from ..ports import Services
from ..registry import command


@dataclass(frozen=True)
class Args:
    query: str
    limit: int = 10


@command(
    "search_projects",
    args=Args,
    description="Search Odoo project.project records by name substring. "
    "Returns a list of {id, name} candidate dicts for disambiguation.",
)
def handle(services: Services, args: Args) -> list[dict[str, Any]]:
    """Return projects whose name matches ``query``.

    :param query: Project name substring to search for.
    :param limit: Maximum number of candidates to return.
    :return: List of ``{"id", "name"}`` project candidate dicts.
    """
    return name_search_projects(services.client, args.query, limit=args.limit)
