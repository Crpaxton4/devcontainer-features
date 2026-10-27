"""Search tasks within a project by name, returning candidate id/name dicts.

Atomic building block for task selection. Disambiguation between multiple
candidates is performed by the interaction surface, not by this command."""

from dataclasses import dataclass
from typing import Any
from odoo_dev_core.services.odoo_helpers import name_search_tasks

from ..ports import Services
from ..registry import command


@dataclass(frozen=True)
class Args:
    query: str
    project_id: int
    limit: int = 10


@command(
    "search_tasks",
    args=Args,
    description="Search Odoo project.task records by name substring within a project. "
    "Returns a list of {id, name} candidate dicts for disambiguation.",
)
def handle(services: Services, args: Args) -> list[dict[str, Any]]:
    """Return tasks in ``project_id`` whose name matches ``query``.

    :param query: Task name substring to search for.
    :param project_id: Odoo project id to scope the search to.
    :param limit: Maximum number of candidates to return.
    :return: List of ``{"id", "name"}`` task candidate dicts.
    """
    return name_search_tasks(
        services.client, args.query, args.project_id, limit=args.limit
    )
