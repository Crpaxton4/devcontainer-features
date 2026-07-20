from ..command import Command
from ._registration import builtin_command
from odoo_sdk.utilities.odoo_helpers import get_unbilled_hours


@builtin_command
class UnbilledHoursCommand(Command):
    _name = "unbilled_hours"
    _description = (
        "Report Odoo timesheet hours that have been logged but not yet invoiced "
        "(read-only). A fields_get capability probe on account.analytic.line "
        "decides the semantics: when both 'timesheet_invoice_id' and "
        "'timesheet_invoice_type' exist (Sales-Timesheet integration), a line is "
        "unbilled when timesheet_invoice_id = False and each row carries its "
        "'invoice_type'; when only one field exists, unbilled falls back to "
        "so_line = False (not linked to a sale order) and 'invoice_type' is "
        "omitted; when neither exists the tool raises a clear error. Optional "
        "start_date/end_date are inclusive 'YYYY-MM-DD' bounds and project_id "
        "restricts to one project.project. Returns a summary envelope "
        "{mode, count, total_hours, lines}; hours are decimal hours."
    )

    def execute(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        project_id: int | None = None,
    ) -> dict:
        return get_unbilled_hours(
            self._client,
            start_date=start_date,
            end_date=end_date,
            project_id=project_id,
        )
