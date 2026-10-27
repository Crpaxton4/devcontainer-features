"""Bill derived sessions to Odoo through the shared upload loop (issue #354).

The CLI ``upload`` subcommand and the TUI ``u`` key both run this handler, so a
non-interactive invocation bills exactly the rows the TUI would. It is CLI-only:
no MCP tool bills time.
"""

from dataclasses import dataclass
from typing import Any, Optional

from ..billing.upload import upload_sessions
from ..ports import Services
from ..registry import command
from . import query_sessions


@dataclass(frozen=True)
class Args:
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    dry_run: bool = False
    sessions: Optional[list[dict[str, Any]]] = None


@command(
    "upload",
    args=Args,
    description=(
        "Bill derived sessions to Odoo timesheets over an optional inclusive "
        "'start_date'/'end_date' range, then retire stale upload mappings. "
        "Idempotent per session key. Pass dry_run=True to preview what would be "
        "billed without writing to Odoo. Pass 'sessions' to bill an explicit "
        "session list instead of the ones derived for the range."
    ),
    surfaces=frozenset({"cli"}),
)
def handle(services: Services, args: Args) -> dict[str, Any]:
    """Bill the derived (or given) sessions and return the upload summary.

    :param start_date: Inclusive ISO start date (``YYYY-MM-DD``), or None.
    :param end_date: Inclusive ISO end date, or None.
    :param dry_run: When True, select and summarise but write nothing to Odoo.
    :param sessions: ``query_sessions`` result dicts to bill; when ``None`` the
        sessions overlapping the range are derived.
    :return: The :func:`~odoo_dev_core.billing.upload.upload_sessions` summary.
    """
    sessions = args.sessions
    if sessions is None:
        sessions = query_sessions.handle(
            services,
            query_sessions.Args(
                start_date=args.start_date,
                end_date=args.end_date,
                include_events=False,
            ),
        )
    return upload_sessions(
        services.client,
        services.events,
        sessions,
        start_date=args.start_date,
        end_date=args.end_date,
        dry_run=args.dry_run,
        config=services.config,
    )
