"""Builtin command that queries global sessions overlapping a date range.

Sessions are detected globally over the whole event stream, so a query never
recomputes boundaries: it returns whole sessions that *overlap* the requested
range (``started_at <= end AND ended_at >= start``) at their true global bounds.
A cross-day or cross-range session therefore reads identically regardless of the
window it is queried through, and its linked events are returned alongside it.
"""

from __future__ import annotations

from typing import Any, Optional

from odoo_sdk.state import SessionWindow, session_key
from odoo_sdk.utilities.upload import range_bounds

from ..command import Command
from ._registration import builtin_command


@builtin_command
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
        :param strategy_name: Restrict to one strategy (``development`` or
            ``review``, #378 item 6), or None for all.
        :param include_events: When True, embed each session's linked events.
        :return: A list of session dicts ordered by start time.
        """
        # The shared range_bounds keeps the query window and the upload path's
        # orphan-sweep window on one inclusive-date semantic (see #354).
        lo, hi = range_bounds(start_date, end_date)
        sessions = self.state.derive_sessions_overlapping(
            lo,
            hi,
            gap_secs=self.config.session_gap_secs,
            task_id=task_id,
            repo=repo,
        )
        # A session's strategy is a per-group label the derivation now computes
        # (development-family vs review-family), so the strategy filter is applied
        # over the derived windows rather than short-circuiting the whole query.
        return [
            self._render(session, include_events)
            for session in sessions
            if strategy_name is None or session.strategy_name == strategy_name
        ]

    def _render(self, session: SessionWindow, include_events: bool) -> dict[str, Any]:
        """Render one session (and optionally its events) as a summary dict."""
        summary: dict[str, Any] = {
            "session_id": session.id,
            "session_key": session_key(session),
            "task_id": session.task_id,
            "repo": session.repo,
            "strategy_name": session.strategy_name,
            "category": session.category,
            "started_at": session.started_at.isoformat(),
            "ended_at": session.ended_at.isoformat(),
            "duration_secs": session.duration_seconds,
        }
        if include_events:
            summary["events"] = self._events_for(session.event_ids)
        return summary

    def _events_for(self, event_ids: tuple[int, ...]) -> list[dict[str, Any]]:
        """Return the derived session's events as summary dicts, in order."""
        return [
            {
                "event_id": record.id,
                "source": record.source,
                "timestamp": record.timestamp.isoformat(),
                "task_ids": record.task_ids,
                "repo": record.repo,
            }
            for record in self.state.get_events_by_ids(list(event_ids))
        ]
