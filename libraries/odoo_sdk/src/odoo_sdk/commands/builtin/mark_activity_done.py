from typing import Any

from ..command import Command
from ._registration import builtin_command
from odoo_sdk.utilities.activities import mark_activity_done


@builtin_command
class MarkActivityDoneCommand(Command):
    """Complete a ``mail.activity`` through Odoo's ``action_feedback``."""

    _name = "mark_activity_done"
    _description = (
        "Mark an Odoo activity (mail.activity) done, wrapping Odoo's "
        "action_feedback(feedback=...). The optional 'feedback' text is posted "
        "to the record's chatter as the completion note. Completing an activity "
        "DELETES the mail.activity record — the chatter message is what remains "
        "— so the activity is read before it is closed and returned in full, "
        "with 'done': true, the feedback, and the id of the chatter message "
        "Odoo posted (null when it posted none). An unknown or already-completed "
        "activity id returns a clear error rather than silently succeeding."
    )

    def execute(self, activity_id: int, feedback: str = "") -> dict[str, Any]:
        """Complete ``activity_id``, returning the activity that was closed.

        :param activity_id: ``mail.activity`` record id to complete.
        :param feedback: Completion note posted to the record's chatter.
        :return: The completed activity plus ``done``, ``feedback``, and
            ``message_id``.
        :raises ValueError: When no activity with ``activity_id`` exists (a
            wrong id, or one already marked done).
        """
        return mark_activity_done(self._client, activity_id, feedback)
