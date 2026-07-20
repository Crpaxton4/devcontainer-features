from ..command import Command
from ._registration import builtin_command
from odoo_sdk.utilities.odoo_helpers import get_task_chatter, get_task_detail


@builtin_command
class GetTaskCommand(Command):
    _name = "get_task"
    _description = (
        "Fetch task context for AI-assisted implementation. Base identity "
        "fields (name, project, stage, assignees, deadline, priority, tags) are "
        "always returned. Extra detail is opt-in via ``include``, a list of any "
        "of: 'description' (task body as Markdown), 'chatter' (all messages as "
        "Markdown), 'dependencies' (blocked_by + blocks tasks), 'timesheets' "
        "(logged time entries), 'subtasks' (child tasks). When ``include`` is "
        "omitted the cheap default is description only. Request extra detail "
        "only when you need it, to keep the call fast."
    )

    def execute(self, task_id: int, include: list[str] | None = None) -> dict | None:
        task = get_task_detail(self._client, task_id, include=include)
        if task is None:
            return None
        if include is not None and "chatter" in include:
            task["chatter"] = get_task_chatter(self._client, task_id)
        return task
