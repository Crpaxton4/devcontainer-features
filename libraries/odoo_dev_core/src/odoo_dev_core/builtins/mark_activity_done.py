"""Complete a ``mail.activity`` through Odoo's ``action_feedback``."""

from dataclasses import dataclass
from typing import Any
from odoo_dev_core.services.activities import mark_activity_done

from ..ports import Services
from ..registry import command


@dataclass(frozen=True)
class Args:
    activity_id: int
    feedback: str = ""


@command(
    "mark_activity_done",
    args=Args,
    description="Mark an Odoo activity (mail.activity) done, wrapping Odoo's "
    "action_feedback(feedback=...). The optional 'feedback' text is posted "
    "to the record's chatter as the completion note. Completing an activity "
    "DELETES the mail.activity record — the chatter message is what remains "
    "— so the activity is read before it is closed and returned in full, "
    "with 'done': true, the feedback, and the id of the chatter message "
    "Odoo posted (null when it posted none). An unknown or already-completed "
    "activity id returns a clear error rather than silently succeeding.",
)
def handle(services: Services, args: Args) -> dict[str, Any]:
    """Complete ``activity_id``, returning the activity that was closed.

    :param activity_id: ``mail.activity`` record id to complete.
    :param feedback: Completion note posted to the record's chatter.
    :return: The completed activity plus ``done``, ``feedback``, and
        ``message_id``.
    :raises ValueError: When no activity with ``activity_id`` exists (a
        wrong id, or one already marked done).
    """
    return mark_activity_done(services.client, args.activity_id, args.feedback)
