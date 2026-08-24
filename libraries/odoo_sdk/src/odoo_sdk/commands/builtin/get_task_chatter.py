from ..command import Command
from ._registration import builtin_command
from odoo_sdk.utilities.odoo_helpers import get_task_chatter


@builtin_command
class GetTaskChatterCommand(Command):
    _name = "get_task_chatter"
    _description = (
        "Fetch chatter messages for an Odoo project.task, returned in "
        "chronological (oldest-first) order. When the chatter holds more "
        "messages than 'limit', the NEWEST ones are kept (#624). Pass 'since' "
        "to fetch only messages after a cursor: an integer is a message-id "
        "cursor (the 'id' of the last entry you saw), a string is an Odoo "
        "datetime cursor. Message bodies are converted from HTML to Markdown. "
        "Includes all message types: comments, notes, and system notifications."
    )

    def execute(
        self, task_id: int, limit: int = 100, since: int | str | None = None
    ) -> list[dict]:
        return get_task_chatter(self._client, task_id, limit=limit, since=since)
