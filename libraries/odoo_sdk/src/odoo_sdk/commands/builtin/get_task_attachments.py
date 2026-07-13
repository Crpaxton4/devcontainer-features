from ..command import Command
from ._registration import builtin_command
from odoo_sdk.utilities.attachments import get_task_attachments


@builtin_command
class GetTaskAttachmentsCommand(Command):
    _name = "get_task_attachments"
    _description = (
        "List an Odoo project.task's attachments from both the task itself and "
        "its chatter messages, deduped by attachment id. Each entry always "
        "carries metadata: id, name, mimetype, file_size, create_date, and "
        "source ('task' or 'message'). Raw bytes are opt-in via "
        "``include_content``: when False (default) the base64 ``datas`` payload "
        "is omitted to keep the call cheap; set True to include it."
    )

    def execute(self, task_id: int, include_content: bool = False) -> list[dict]:
        return get_task_attachments(
            self._client, task_id, include_content=include_content
        )
