from ..command import Command
from ._registration import builtin_command
from odoo_sdk.billing.timesheet import resolve_employee_id


@builtin_command
class GetEmployeeIdCommand(Command):
    """Return the ``hr.employee`` id of the authenticated Odoo user.

    Delegates to :func:`odoo_sdk.billing.timesheet.resolve_employee_id` rather
    than reimplementing the lookup, so the billing writer and any export path
    share exactly one answer to "who is this?" (issue #499). The resolver
    derives the employee from the authenticated uid and caches it in local
    state, so this command self-heals when the cache is cleared.
    """

    _name = "get_employee_id"
    _description = (
        "Get the hr.employee id for the current user. Derived from the "
        "authenticated Odoo uid and cached in local state; used to stamp an "
        "explicit Employee on exported timesheet rows."
    )

    def execute(self) -> int:
        """Return the authenticated user's ``hr.employee`` id."""
        return resolve_employee_id(self._client, self.state)
