"""List project tasks, optionally narrowed by an Odoo domain filter."""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from odoo_dev_core.services.odoo_helpers import get_task_detail

from ..ports import Services
from ..registry import command


@dataclass(frozen=True)
class Args:
    domain: Optional[List[Tuple[str, str, Any]]] = None
    limit: int = 10
    include: Optional[List[str]] = None


@command(
    "get_tasks",
    args=Args,
    description="Lists project tasks with an optional domain filter. Per-task detail "
    "is opt-in via ``include``, mirroring get_task's contract: a list of "
    "any of 'description' (task body as Markdown), 'dependencies' "
    "(blocked_by + blocks tasks), 'timesheets' (logged time entries), "
    "'subtasks' (child tasks) — applied to each result, so a task list "
    "with descriptions costs one call instead of one get_task per task. "
    "When ``include`` is omitted only the cheap summary fields are "
    "returned.",
)
def handle(services: Services, args: Args) -> List[Dict[str, Any]]:
    """Search ``project.task`` and return summary fields for each match.

    When ``include`` is provided, each match is expanded through the same
    detail fetch that backs ``get_task`` (:func:`get_task_detail`), so
    e.g. ``include=["description"]`` returns descriptions for the whole
    batch in one call. When ``include`` is omitted the cheap summary-only
    behavior is unchanged.
    """
    task_domain = args.domain or []
    matches = services.client["project.task"].search(task_domain, limit=args.limit)
    if args.include is not None:
        details = (
            get_task_detail(services.client, task_id, include=args.include)
            for task_id in matches.ids
        )
        # A task deleted between the search and the per-task read yields
        # None; drop it rather than surfacing holes in the batch.
        return [detail for detail in details if detail is not None]
    fields_to_fetch = [
        "name",
        "project_id",
        "stage_id",
        "user_ids",
        "date_deadline",
    ]
    return matches.read(fields_to_fetch)
