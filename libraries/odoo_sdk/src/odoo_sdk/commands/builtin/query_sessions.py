"""Builtin command that queries global sessions overlapping a date range.

Sessions are detected globally over the whole event stream, so a query never
recomputes boundaries: it returns whole sessions that *overlap* the requested
range (``started_at <= end AND ended_at >= start``) at their true global bounds.
A cross-day or cross-range session therefore reads identically regardless of the
window it is queried through, and its linked events are returned alongside it.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Optional

from odoo_sdk.state import SessionWindow

from ..command import Command


def _parse_date(value: Optional[str]) -> Optional[date]:
    """Parse an ISO ``YYYY-MM-DD`` string into a :class:`date`, or None."""
    return date.fromisoformat(value) if value else None


def _range_bounds(
    start_date: Optional[str], end_date: Optional[str]
) -> tuple[datetime, datetime]:
    """Resolve inclusive ISO date strings into a ``[start, end]`` datetime pair.

    ``end`` is midnight of the day *after* ``end_date`` so the whole end day is
    covered. When a bound is omitted it defaults to the widest representable
    range so callers can query "everything".
    """
    start = _parse_date(start_date)
    end = _parse_date(end_date)
    lo = datetime(start.year, start.month, start.day) if start else datetime.min
    if end is None:
        return lo, datetime.max
    nxt = end + timedelta(days=1)
    return lo, datetime(nxt.year, nxt.month, nxt.day)


class QuerySessionsCommand(Command):
    """Return global sessions overlapping a date range, with their events.

    Boundaries are never recomputed by the query: sessions are returned whole,
    at their true global bounds, so the same session reads identically through
    any overlapping window. Optional ``task_id``, ``repo``, and ``strategy_name``
    filters narrow the result.
    """

    _name = "query_sessions"
    _description = (
        "Query global cross-day sessions overlapping a date range, returned "
        "whole with their linked events and optional task/repo/strategy filters."
    )

    def execute(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        task_id: Optional[str] = None,
        repo: Optional[str] = None,
        strategy_name: Optional[str] = None,
        include_events: bool = True,
    ) -> list[dict[str, Any]]:
        """Return overlapping sessions as summary dicts.

        :param start_date: Inclusive ISO start date (``YYYY-MM-DD``), or None.
        :param end_date: Inclusive ISO end date (``YYYY-MM-DD``), or None.
        :param task_id: Restrict to one task id, or None for all.
        :param repo: Restrict to one repo, or None for all.
        :param strategy_name: Restrict to one strategy, or None for all.
        :param include_events: When True, embed each session's linked events.
        :return: A list of session dicts ordered by start time.
        """
        lo, hi = _range_bounds(start_date, end_date)
        sessions = self.state.get_sessions_overlapping(
            lo,
            hi,
            task_id=task_id,
            repo=repo,
            strategy_name=strategy_name,
        )
        return [self._render(session, include_events) for session in sessions]

    def _render(self, session: SessionWindow, include_events: bool) -> dict[str, Any]:
        """Render one session (and optionally its events) as a summary dict."""
        summary: dict[str, Any] = {
            "session_id": session.id,
            "task_id": session.task_id,
            "repo": session.repo,
            "strategy_name": session.strategy_name,
            "category": session.category,
            "started_at": session.started_at.isoformat(),
            "ended_at": session.ended_at.isoformat(),
            "duration_secs": session.duration_seconds,
        }
        if include_events:
            summary["events"] = self._events_for(session.id)
        return summary

    def _events_for(self, session_id: Optional[int]) -> list[dict[str, Any]]:
        """Return the linked events for one session as summary dicts."""
        if session_id is None:
            return []
        return [
            {
                "event_id": record.id,
                "source": record.source,
                "timestamp": record.timestamp.isoformat(),
                "task_ids": record.task_ids,
                "repo": record.repo,
            }
            for record in self.state.get_events_for_session(session_id)
        ]
