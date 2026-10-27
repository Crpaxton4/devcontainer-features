from dataclasses import dataclass
from odoo_dev_core.services.attachments import get_task_attachments

from ..ports import Services
from ..registry import command


@dataclass(frozen=True)
class Args:
    task_id: int
    include_content: bool = False


@command(
    "get_task_attachments",
    args=Args,
    description="List an Odoo project.task's attachments from both the task itself and "
    "its chatter messages, deduped by attachment id. Each entry always "
    "carries metadata: id, name, mimetype, file_size, create_date, and "
    "source ('task' or 'message'). Raw bytes are opt-in via "
    "``include_content``: when False (default) the base64 ``datas`` payload "
    "is omitted to keep the call cheap; set True to include it.",
)
def handle(services: Services, args: Args) -> list[dict]:
    return get_task_attachments(
        services.client, args.task_id, include_content=args.include_content
    )
