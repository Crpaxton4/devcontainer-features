from ..command import Command
from ._registration import builtin_command
from odoo_sdk.utilities.mail_status import get_mail_status


@builtin_command
class GetMailStatusCommand(Command):
    _name = "get_mail_status"
    _description = (
        "Report outgoing-mail (mail.mail) delivery status for one Odoo record "
        "(read-only). Joins the record's chatter messages to their linked "
        "outbound mails and returns, per mail: mail_id, message_id, subject, a "
        "recipients summary, the delivery state (outgoing/sent/exception/cancel), "
        "the message date, and — only when populated — failure_reason / "
        "failure_type. Use it to verify 'send an email' acceptance criteria: pass "
        "res_model='project.task' with the task id to check a task's outbound "
        "mail. Records with only chatter notes return an empty list. Never "
        "retries or requeues mail. mail.mail is often admin-restricted; a denied "
        "read returns a clear access error."
    )

    def execute(self, res_model: str, res_id: int) -> list[dict]:
        """Return the outgoing-mail status entries for ``res_model``/``res_id``.

        :param res_model: The record's model, e.g. ``"project.task"``.
        :param res_id: The record's id.
        :return: One status entry per linked ``mail.mail``, oldest first.
        :raises ValueError: When reading ``mail.mail`` is denied.
        """
        return get_mail_status(self._client, res_model, res_id)
