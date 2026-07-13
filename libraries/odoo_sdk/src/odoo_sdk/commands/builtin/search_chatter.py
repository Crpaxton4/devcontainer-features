from typing import Any, Dict, List, Optional

from ..command import Command
from ._registration import builtin_command
from odoo_sdk.utilities.odoo_helpers import search_chatter


@builtin_command
class SearchChatterCommand(Command):
    _name = "search_chatter"
    _description = (
        "Full-text search across Odoo chatter (mail.message) conversation "
        "bodies. Matches the query case-insensitively against message bodies "
        "(body ilike) and returns the newest matches first. Optional filters "
        "narrow the search to one model (e.g. 'project.task'), one record, "
        "and/or a date range (YYYY-MM-DD). Read-only. Each result carries the "
        "author, timestamp, HTML-stripped Markdown body, and the originating "
        "res_model/res_id so the record can be located."
    )

    def execute(
        self,
        query: str,
        model: Optional[str] = None,
        record_id: Optional[int] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        return search_chatter(
            self._client,
            query,
            model=model,
            record_id=record_id,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
        )
