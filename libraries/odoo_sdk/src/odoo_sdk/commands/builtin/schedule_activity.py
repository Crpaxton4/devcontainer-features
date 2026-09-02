from typing import Any, Optional, Union

from ..command import Command
from ._registration import builtin_command
from odoo_sdk.utilities.activities import (
    DEFAULT_ACTIVITY_RES_MODEL,
    schedule_activity,
)


@builtin_command
class ScheduleActivityCommand(Command):
    """Create a ``mail.activity`` (a scheduled follow-up) on an Odoo record."""

    _name = "schedule_activity"
    _description = (
        "Schedule an Odoo activity (mail.activity) on a record: the 'To Do' / "
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
        "type and assignee. Scheduling requires read access on ir.model, since "
        "mail.activity stores its target model as a mandatory ir.model "
        "reference; a denied read returns a clear access error."
    )

    def execute(
        self,
        res_id: int,
        res_model: str = DEFAULT_ACTIVITY_RES_MODEL,
        activity_type: Optional[Union[int, str]] = None,
        summary: str = "",
        note: str = "",
        date_deadline: Optional[str] = None,
        user_id: Optional[int] = None,
    ) -> dict[str, Any]:
        """Create one activity on ``res_model``/``res_id`` and return it.

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
            type, an unknown ``res_model``, or a denied ``ir.model`` read.
        """
        return schedule_activity(
            self._client,
            res_id,
            res_model=res_model,
            activity_type=activity_type,
            summary=summary,
            note=note,
            date_deadline=date_deadline,
            user_id=user_id,
        )
