from dataclasses import dataclass
from odoo_dev_core.billing.timesheet_reports import timesheet_summary

from ..ports import Services
from ..registry import command


@dataclass(frozen=True)
class Args:
    start_date: str
    end_date: str
    group_by: str = "project"
    only_mine: bool = True


@command(
    "timesheet_summary",
    args=Args,
    description="Summarize logged timesheet hours (account.analytic.line) over an "
    "inclusive date range, collapsed onto one axis. Read-only. Dates are "
    "'YYYY-MM-DD'; hours are the unit (Odoo 'unit_amount'). 'group_by' is "
    "one of: 'project' (by each task's project), 'client' (by each "
    "project's partner/customer), 'task' (by individual task), or 'day' (by "
    "calendar day). 'only_mine=True' restricts to the authenticated user's "
    "own employee timesheets; False covers every timesheet the user can "
    "see. Returns per-group hours and entry counts plus a grand total_hours.",
)
def handle(services: Services, args: Args) -> dict:
    return timesheet_summary(
        services.client,
        args.start_date,
        args.end_date,
        group_by=args.group_by,
        only_mine=args.only_mine,
    )
