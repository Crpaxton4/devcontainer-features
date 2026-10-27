from dataclasses import dataclass
from odoo_dev_core.services.odoo_helpers import get_task_chatter, get_task_detail

from ..ports import Services
from ..registry import command


@dataclass(frozen=True)
class Args:
    task_id: int
    include: list[str] | None = None


@command(
    "get_task",
    args=Args,
    description="Fetch task context for AI-assisted implementation. Base identity "
    "fields (name, project, stage, assignees, deadline, priority, tags) are "
    "always returned. Extra detail is opt-in via ``include``, a list of any "
    "of: 'description' (task body as Markdown), 'chatter' (all messages as "
    "Markdown), 'dependencies' (blocked_by + blocks tasks), 'timesheets' "
    "(logged time entries), 'subtasks' (child tasks). When ``include`` is "
    "omitted the cheap default is description only. Request extra detail "
    "only when you need it, to keep the call fast.",
)
def handle(services: Services, args: Args) -> dict | None:
    task = get_task_detail(services.client, args.task_id, include=args.include)
    if task is None:
        return None
    if args.include is not None and "chatter" in args.include:
        task["chatter"] = get_task_chatter(services.client, args.task_id)
    return task
