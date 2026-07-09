from ..command import Command
from odoo_sdk.utilities.odoo_helpers import get_task_chatter, get_task_detail


class GetTaskCommand(Command):
    _name = "get_task"
    _description = (
        "Fetch full task context for AI-assisted implementation: task fields "
        "(name, description in Markdown, project, stage, assignees, deadline) "
        "plus all chatter messages with bodies converted to Markdown. "
        "Use at the start of a work session to load complete task context."
    )

    def execute(self, task_id: int) -> dict | None:
        task = get_task_detail(self._client, task_id)
        if task is None:
            return None
        task["chatter"] = get_task_chatter(self._client, task_id)
        return task
