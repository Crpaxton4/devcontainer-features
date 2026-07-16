"""Pure functional and Odoo-operation helpers shared across commands.

The utilities layer holds reusable helpers that commands compose. Pure helpers
(``html_to_markdown``, ``resolve_many2one``, ``format_chatter``) take and return
only primitives; the remaining thin Odoo wrappers issue a single client call so
command bodies stay at one altitude.
"""

from .env import OdooDevcontainerRequiredError, assert_odoo_devcontainer
from .html import html_to_markdown
from .odoo_helpers import (
    create_timesheet,
    format_chatter,
    get_employee_id,
    get_task_chatter,
    get_task_detail,
    name_search_projects,
    name_search_tasks,
    post_chatter_note,
    resolve_many2one,
)

__all__ = [
    "assert_odoo_devcontainer",
    "OdooDevcontainerRequiredError",
    "html_to_markdown",
    "resolve_many2one",
    "format_chatter",
    "name_search_projects",
    "name_search_tasks",
    "get_employee_id",
    "create_timesheet",
    "post_chatter_note",
    "get_task_chatter",
    "get_task_detail",
]
