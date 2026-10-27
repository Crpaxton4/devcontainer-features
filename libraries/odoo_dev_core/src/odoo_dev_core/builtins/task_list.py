"""List project tasks assigned to the current user."""

from dataclasses import dataclass
from typing import Any, Optional
from odoo_dev_core.services.odoo_helpers import name_search_projects

from ..ports import Services
from ..registry import command


@dataclass(frozen=True)
class Args:
    project_name_query: Optional[str] = None
    stage: Optional[str] = None
    limit: int = 20


@command(
    "task_list",
    args=Args,
    description="List project.task records assigned to the authenticated user, "
    "optionally filtered by project name substring and/or stage name.",
)
def handle(services: Services, args: Args) -> list[dict[str, Any]]:
    """Search tasks assigned to the current user.

    :param project_name_query: Fuzzy project name to filter by.
    :param stage: Stage name substring to filter by.
    :param limit: Maximum number of tasks to return.
    :return: List of matching task records.
    """
    uid = services.client.uid
    domain: list[Any] = [("user_ids", "in", [uid])]

    if args.project_name_query:
        projects = name_search_projects(
            services.client, args.project_name_query, limit=5
        )
        if not projects:
            return []
        project_ids = [p["id"] for p in projects]
        domain.append(("project_id", "in", project_ids))

    if args.stage:
        domain.append(("stage_id.name", "ilike", args.stage))

    return services.client.execute(
        "project.task",
        "search_read",
        domain,
        fields=[
            "id",
            "name",
            "project_id",
            "stage_id",
            "date_deadline",
            "user_ids",
        ],
        limit=args.limit,
    )
