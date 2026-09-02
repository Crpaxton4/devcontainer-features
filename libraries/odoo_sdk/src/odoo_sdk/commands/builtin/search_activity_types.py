from typing import Any, Optional

from ..command import Command
from ._registration import builtin_command
from odoo_sdk.utilities.activities import (
    DEFAULT_ACTIVITY_TYPE_LIMIT,
    search_activity_types,
)


@builtin_command
class SearchActivityTypesCommand(Command):
    """Search the ``mail.activity.type`` records available for scheduling."""

    _name = "search_activity_types"
    _description = (
        "List the Odoo activity types (mail.activity.type) available for "
        "scheduling — 'To Do', 'Call', 'Meeting', 'Email', plus whatever the "
        "database defines — ordered by name (read-only). The discovery "
        "counterpart to schedule_activity's 'activity_type' parameter, which "
        "also accepts a name directly; call this when the exact spelling is "
        "unknown or a name came back ambiguous. 'query' is a case-insensitive "
        "substring match on the type name; 'res_model' narrows the list to the "
        "types applicable to that document type (the generic types plus any "
        "scoped to exactly that model). Each result carries id, name, and "
        "res_model (null for a generic type)."
    )

    def execute(
        self,
        query: Optional[str] = None,
        res_model: Optional[str] = None,
        limit: int = DEFAULT_ACTIVITY_TYPE_LIMIT,
    ) -> list[dict[str, Any]]:
        """Return the activity types matching ``query``/``res_model``.

        :param query: Case-insensitive substring matched against the type name.
        :param res_model: Restrict to types applicable to this model.
        :param limit: Maximum number of types to return.
        :return: ``{"id", "name", "res_model"}`` dicts, name-ordered.
        """
        return search_activity_types(
            self._client, query=query, res_model=res_model, limit=limit
        )
