from ..command import Command
from ._registration import builtin_command
from odoo_sdk.utilities.timesheets import timesheet_summary


@builtin_command
class TimesheetSummaryCommand(Command):
    _name = "timesheet_summary"
    _description = (
        "Summarize logged timesheet hours (account.analytic.line) over an "
        "inclusive date range, collapsed onto one axis. Read-only. Dates are "
        "'YYYY-MM-DD'; hours are the unit (Odoo 'unit_amount'). 'group_by' is "
        "one of: 'project' (by each task's project), 'client' (by each "
        "project's partner/customer), 'task' (by individual task), or 'day' (by "
        "calendar day). 'only_mine=True' restricts to the authenticated user's "
        "own employee timesheets; False covers every timesheet the user can "
        "see. Returns per-group hours and entry counts plus a grand total_hours."
    )

    def execute(
        self,
        start_date: str,
        end_date: str,
        group_by: str = "project",
        only_mine: bool = True,
    ) -> dict:
        return timesheet_summary(
            self._client,
            start_date,
            end_date,
            group_by=group_by,
            only_mine=only_mine,
        )
