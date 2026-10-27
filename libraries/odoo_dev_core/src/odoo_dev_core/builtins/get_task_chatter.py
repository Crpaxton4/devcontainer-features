from dataclasses import dataclass
from odoo_dev_core.services.odoo_helpers import get_task_chatter

from ..ports import Services
from ..registry import command


@dataclass(frozen=True)
class Args:
    task_id: int
    limit: int = 100
    since: int | str | None = None


@command(
    "get_task_chatter",
    args=Args,
    description="Fetch chatter messages for an Odoo project.task, returned in "
    "chronological (oldest-first) order. When the chatter holds more "
    "messages than 'limit', the NEWEST ones are kept (#624). Pass 'since' "
    "to fetch only messages after a cursor: an integer is a message-id "
    "cursor (the 'id' of the last entry you saw), a string is an Odoo "
    "datetime cursor. Message bodies are converted from HTML to Markdown. "
    "Includes all message types: comments, notes, and system notifications.",
)
def handle(services: Services, args: Args) -> list[dict]:
    return get_task_chatter(
        services.client, args.task_id, limit=args.limit, since=args.since
    )
