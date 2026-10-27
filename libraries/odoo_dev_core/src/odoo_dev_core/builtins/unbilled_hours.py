from dataclasses import dataclass
from odoo_dev_core.services.odoo_helpers import get_unbilled_hours

from ..ports import Services
from ..registry import command


@dataclass(frozen=True)
class Args:
    start_date: str | None = None
    end_date: str | None = None
    project_id: int | None = None


@command(
    "unbilled_hours",
    args=Args,
    description="Report Odoo timesheet hours that have been logged but not yet invoiced "
    "(read-only). A fields_get capability probe on account.analytic.line "
    "decides the semantics: when both 'timesheet_invoice_id' and "
    "'timesheet_invoice_type' exist (Sales-Timesheet integration), a line is "
    "unbilled when timesheet_invoice_id = False and each row carries its "
    "'invoice_type'; when only one field exists, unbilled falls back to "
    "so_line = False (not linked to a sale order) and 'invoice_type' is "
    "omitted; when neither exists the tool raises a clear error. Optional "
    "start_date/end_date are inclusive 'YYYY-MM-DD' bounds and project_id "
    "restricts to one project.project. Returns a summary envelope "
    "{mode, count, total_hours, lines}; hours are decimal hours.",
)
def handle(services: Services, args: Args) -> dict:
    return get_unbilled_hours(
        services.client,
        start_date=args.start_date,
        end_date=args.end_date,
        project_id=args.project_id,
    )
