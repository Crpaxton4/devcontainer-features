"""Report open project tasks ordered by how stale they are (read-only)."""

from dataclasses import dataclass
from typing import Any, Optional
from odoo_dev_core.services.odoo_helpers import get_task_aging

from ..ports import Services
from ..registry import command


@dataclass(frozen=True)
class Args:
    project_id: Optional[int] = None
    stage: Optional[str] = None
    limit: int = 20


@command(
    "task_aging",
    args=Args,
    description="List open Odoo project.task records that are going stale, sorted "
    "stalest-first. 'Open' means the task's kanban stage is not folded "
    "(stage_id.fold = False) and the task is not archived. Each record "
    "carries: task_id, name, project, stage, days_open (whole days since "
    "create_date) and days_in_stage (whole days since "
    "date_last_stage_update; falls back to create_date when that timestamp "
    "is missing/False). Day counts are whole UTC days. Sorted by "
    "days_in_stage descending, ties broken by days_open descending. "
    "Optional project_id filters by exact project id; optional stage is a "
    "case-insensitive substring match on the stage's display name; limit "
    "(default 20) bounds the number of tasks returned. Read-only.",
)
def handle(services: Services, args: Args) -> list[dict[str, Any]]:
    """Return open tasks ordered stalest-first.

    :param project_id: Restrict to one project id, or ``None`` for all.
    :param stage: Case-insensitive stage-name substring filter, or ``None``.
    :param limit: Maximum number of tasks to return.
    :return: Aging records (``task_id``, ``name``, ``project``, ``stage``,
        ``days_open``, ``days_in_stage``), stalest-first.
    """
    return get_task_aging(
        services.client, project_id=args.project_id, stage=args.stage, limit=args.limit
    )
