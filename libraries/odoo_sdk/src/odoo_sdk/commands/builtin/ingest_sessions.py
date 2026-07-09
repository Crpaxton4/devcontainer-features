"""Builtin command that ingests stored events into global sessions incrementally.

Global sessions are detected over the whole event stream per ``(task, repo,
strategy)`` group and maintained incrementally through the persisted event →
session link. This command ingests a batch of stored events into that structure
using the *fixed* configured gap (a stable session-identity constant), never a
per-run sweep. It is idempotent and its cost scales with the batch, not history.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Optional

from odoo_sdk.adapters import ingest_events_incrementally

from ..command import Command


def _parse_date(value: Optional[str]) -> Optional[date]:
    """Parse an ISO ``YYYY-MM-DD`` string into a :class:`date`, or None."""
    return date.fromisoformat(value) if value else None


def _start_bound(value: Optional[date]) -> Optional[datetime]:
    """Turn an inclusive start date into a midnight ``datetime``, or None."""
    return datetime(value.year, value.month, value.day) if value else None


def _end_bound(value: Optional[date]) -> Optional[datetime]:
    """Turn an inclusive end date into an exclusive next-midnight bound, or None."""
    if value is None:
        return None
    nxt = value + timedelta(days=1)
    return datetime(nxt.year, nxt.month, nxt.day)


class IngestSessionsCommand(Command):
    """Ingest stored events into the incrementally-maintained global sessions.

    Reads events from the local ``events`` table (optionally range-bounded),
    then updates the ``sessions`` table and the event → session links in place
    using the fixed gap from :class:`~odoo_sdk.state.LocalConfig`. Only the
    affected groups' local neighborhoods are touched, so repeated ingests do
    work proportional to the new events, not the full history.
    """

    _name = "ingest_sessions"
    _description = (
        "Ingest stored events into global cross-day sessions incrementally, "
        "maintaining event-to-session links with the fixed configured gap."
    )

    def execute(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> dict[str, Any]:
        """Ingest events in the given inclusive date range (default: all).

        :param start_date: Inclusive ISO start date (``YYYY-MM-DD``), or None.
        :param end_date: Inclusive ISO end date (``YYYY-MM-DD``), or None. The
            bound is exclusive of the following day so the whole end day is
            included.
        :return: A summary with the events considered, sessions created, and the
            gap used.
        """
        start = _start_bound(_parse_date(start_date))
        end = _end_bound(_parse_date(end_date))
        records = self.state.get_events(start, end)
        gap_secs = self.config.session_gap_secs
        created = ingest_events_incrementally(self.state, records, gap_secs)
        return {
            "events_considered": len(records),
            "sessions_created": created,
            "gap_mins": self.config.session_gap_mins,
        }
