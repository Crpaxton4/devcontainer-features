"""Create a ``mail.activity`` (a scheduled follow-up) on an Odoo record."""

from dataclasses import dataclass
from typing import Any, Optional, Union
from odoo_dev_core.services.activities import (
    DEFAULT_ACTIVITY_RES_MODEL,
    schedule_activity,
)

from ..ports import Services
from ..registry import command


@dataclass(frozen=True)
class Args:
    res_id: int
    res_model: str = DEFAULT_ACTIVITY_RES_MODEL
    activity_type: Optional[Union[int, str]] = None
    summary: str = ""
    note: str = ""
    date_deadline: Optional[str] = None
    user_id: Optional[int] = None


@command(
    "schedule_activity",
    args=Args,
    description="Schedule an Odoo activity (mail.activity) on a record: the 'To Do' / "
    "'Call' / 'Meeting' / 'Email' follow-ups that appear in a record's "
    "activity area and in the assignee's Odoo inbox. 'res_model' defaults "
    "to 'project.task', so scheduling on a task needs only 'res_id'. "
    "'activity_type' takes either a mail.activity.type id or its name "
    "(case-insensitive, e.g. 'Call'); use search_activity_types to see what "
    "the database offers. 'summary' is the short title, 'note' the body "
    "(written in Markdown, rendered to HTML for Odoo), 'date_deadline' an "
    "inclusive 'YYYY-MM-DD' due date (omitted: Odoo applies the activity "
    "type's own delay), and 'user_id' the assignee (omitted: the "
    "authenticated user). Returns the created activity with its resolved "
    "type and assignee. mail.activity stores its target model as a "
    "mandatory ir.model id, which the SDK takes from the hand-managed "
    "[model_ids] section of the Odoo SDK config rather than by reading "
    "ir.model; scheduling against a model that has no [model_ids] entry "
    "fails with an error naming the exact entry an operator must add.",
)
def handle(services: Services, args: Args) -> dict[str, Any]:
    """Create one activity on ``res_model``/``res_id`` and return it.

    The injected ``services.config`` is handed
    to the helper because ``mail.activity.res_model_id`` is resolved from the
    ``[model_ids]`` map rather than by reading ``ir.model`` (#444, #686).

    :param res_id: Id of the record the activity is attached to.
    :param res_model: The record's model; defaults to ``project.task``.
    :param activity_type: ``mail.activity.type`` id, or its name resolved
        case-insensitively (exact match first, then substring).
    :param summary: Short activity title.
    :param note: Activity body in Markdown; rendered to HTML for Odoo.
    :param date_deadline: Inclusive ISO ``YYYY-MM-DD`` due date.
    :param user_id: Assignee ``res.users`` id; defaults to the current uid.
    :return: The created activity, read back with resolved names.
    :raises ValueError: On a malformed deadline, an unresolvable activity
        type, or a ``res_model`` with no ``[model_ids]`` config entry (the
        message names the entry to add).
    """
    return schedule_activity(
        services.client,
        args.res_id,
        res_model=args.res_model,
        activity_type=args.activity_type,
        summary=args.summary,
        note=args.note,
        date_deadline=args.date_deadline,
        user_id=args.user_id,
        config=services.config,
    )
