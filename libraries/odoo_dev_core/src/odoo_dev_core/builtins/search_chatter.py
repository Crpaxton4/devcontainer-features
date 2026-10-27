from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from odoo_dev_core.services.odoo_helpers import search_chatter

from ..ports import Services
from ..registry import command


@dataclass(frozen=True)
class Args:
    query: str
    model: Optional[str] = None
    record_id: Optional[int] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    limit: int = 20


@command(
    "search_chatter",
    args=Args,
    description="Full-text search across Odoo chatter (mail.message) conversation "
    "bodies. Matches the query case-insensitively against message bodies "
    "(body ilike) and returns the newest matches first. Optional filters "
    "narrow the search to one model (e.g. 'project.task'), one record, "
    "and/or a date range (YYYY-MM-DD). Read-only. Each result carries the "
    "author, timestamp, HTML-stripped Markdown body, and the originating "
    "res_model/res_id so the record can be located.",
)
def handle(services: Services, args: Args) -> List[Dict[str, Any]]:
    return search_chatter(
        services.client,
        args.query,
        model=args.model,
        record_id=args.record_id,
        date_from=args.date_from,
        date_to=args.date_to,
        limit=args.limit,
    )
