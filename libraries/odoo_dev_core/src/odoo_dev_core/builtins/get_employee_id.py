"""Return the ``hr.employee`` id of the authenticated Odoo user.

Delegates to :func:`odoo_dev_core.billing.timesheet.resolve_employee_id` rather
than reimplementing the lookup, so the billing writer and any export path
share exactly one answer to "who is this?" (issue #499). The resolver
derives the employee from the authenticated uid and caches it in local
state, so this command self-heals when the cache is cleared."""

from dataclasses import dataclass
from odoo_dev_core.billing.timesheet import resolve_employee_id

from ..ports import Services
from ..registry import command


@dataclass(frozen=True)
class Args:
    pass


@command(
    "get_employee_id",
    args=Args,
    description="Get the hr.employee id for the current user. Derived from the "
    "authenticated Odoo uid and cached in local state; used to stamp an "
    "explicit Employee on exported timesheet rows.",
    surfaces=frozenset({"cli"}),
)
def handle(services: Services, args: Args) -> int:
    """Return the authenticated user's ``hr.employee`` id."""
    return resolve_employee_id(services.client, services.events)
