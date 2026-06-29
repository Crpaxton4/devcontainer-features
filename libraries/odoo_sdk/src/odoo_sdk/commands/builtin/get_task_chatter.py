from ..command import Command
from odoo_sdk.task_tracker.odoo_ops import get_task_chatter


class GetTaskChatterCommand(Command):
    _name = "get_task_chatter"
    _description = (
        "Fetch all chatter messages for an Odoo project.task, sorted oldest-first. "
        "Message bodies are converted from HTML to Markdown. "
        "Includes all message types: comments, notes, and system notifications."
    )

    def execute(self, task_id: int, limit: int = 100) -> list[dict]:
        return get_task_chatter(self._client, task_id, limit=limit)
