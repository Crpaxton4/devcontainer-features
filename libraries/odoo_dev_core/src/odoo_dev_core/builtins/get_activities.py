"""List the open ``mail.activity`` records on a record and/or for a user."""

from dataclasses import dataclass
from typing import Any, Optional
from odoo_dev_core.services.activities import DEFAULT_ACTIVITY_LIMIT, get_activities

from ..ports import Services
from ..registry import command


@dataclass(frozen=True)
class Args:
    res_id: Optional[int] = None
    res_model: Optional[str] = None
    user_id: Optional[int] = None
    limit: int = DEFAULT_ACTIVITY_LIMIT


@command(
    "get_activities",
    args=Args,
    description="List open Odoo activities (mail.activity) on a record and/or for a "
    "user, soonest deadline first (read-only). Every mail.activity is open "
    "by definition — marking one done deletes the record and leaves a "
    "chatter message — so this is the read counterpart to "
    "schedule_activity. A 'res_id' with no 'res_model' is read as a "
    "project.task id; a call with no filters at all scopes to the "
    "authenticated user's own activities rather than returning every open "
    "activity in the database. Each entry carries activity_id, res_model, "
    "res_id, res_name, activity_type (+id), summary, note (as Markdown), "
    "date_deadline, user (+id), Odoo's computed state "
    "(overdue/today/planned), and create_date.",
)
def handle(services: Services, args: Args) -> list[dict[str, Any]]:
    """Return the open activities matching the given filters.

    :param res_id: Restrict to one record's activities.
    :param res_model: The record's model; defaults to ``project.task``
        whenever ``res_id`` is given without one.
    :param user_id: Restrict to one assignee; defaults to the current uid
        when no other filter is supplied.
    :param limit: Maximum number of activities to return.
    :return: Activity entries ordered by ``date_deadline`` ascending.
    """
    return get_activities(
        services.client,
        res_id=args.res_id,
        res_model=args.res_model,
        user_id=args.user_id,
        limit=args.limit,
    )
