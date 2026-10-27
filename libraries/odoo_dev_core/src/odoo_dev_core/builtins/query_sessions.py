"""Builtin command that queries global sessions overlapping a date range.

Sessions are detected globally over the whole event stream, so a query never
recomputes boundaries: it returns whole sessions that *overlap* the requested
range (``started_at <= end AND ended_at >= start``) at their true global bounds.
A cross-day or cross-range session therefore reads identically regardless of the
window it is queried through, and its linked events are returned alongside it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from odoo_dev_data.events import SessionWindow, session_key
from odoo_dev_core.billing.upload import range_bounds
from odoo_dev_core.sessionization.jobs import derive_windows

from ..ports import Services
from ..registry import command


@dataclass(frozen=True)
class Args:
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    task_id: Optional[str] = None
    repo: Optional[str] = None
    strategy_name: Optional[str] = None
    include_events: bool = True


def _render(
    services: Services, session: SessionWindow, include_events: bool
) -> dict[str, Any]:
    """Render one session (and optionally its events) as a summary dict.

    ``repo`` is emitted verbatim, which is ``""`` for a repo-less agent
    session (#508) — an absent value rather than an in-band sentinel, so no
    control character can reach a JSON or MCP consumer. Presentation layers
    that need a printable stand-in call
    :func:`~odoo_dev_data.events.format_repo_label` rather than masking locally.
    """
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
        summary["events"] = _events_for(services, session.event_ids)
    return summary


def _events_for(services: Services, event_ids: tuple[int, ...]) -> list[dict[str, Any]]:
    """Return the derived session's events as audit dicts, in order.

    Carries the reconstructable substance (#626) — ``subject``, ``branch``,
    ``pr_num``, and the enriched ``payload`` — alongside the identity
    fields, so one ``query_sessions`` call yields a chronological audit of
    what happened in the session, not just when.
    """
    return [
        {
            "event_id": record.id,
            "source": record.source,
            "timestamp": record.timestamp.isoformat(),
            "task_ids": record.task_ids,
            "repo": record.repo,
            "branch": record.branch,
            "pr_num": record.pr_num,
            "subject": record.subject,
            "payload": record.payload,
        }
        for record in services.events.get_events_by_ids(list(event_ids))
    ]


@command(
    "query_sessions",
    args=Args,
    description=(
        "Query global cross-day sessions overlapping a date range, returned "
        "whole with their linked events and optional task/repo/strategy filters."
    ),
)
def handle(services: Services, args: Args) -> list[dict[str, Any]]:
    """Return overlapping sessions as summary dicts.

    Boundaries are never recomputed by the query: sessions are returned whole,
    at their true global bounds. Finished dispatcher jobs, when the job port is
    present, fold into the event-derived sessions (see
    :mod:`odoo_dev_core.sessionization.jobs`).

    :param start_date: Inclusive ISO start date (``YYYY-MM-DD``), or None.
    :param end_date: Inclusive ISO end date (``YYYY-MM-DD``), or None.
    :param task_id: Restrict to one task id, or None for all.
    :param repo: Restrict to one repo, or None for all. Pass ``""`` to select
        only repo-less (agentless) sessions.
    :param strategy_name: Restrict to one strategy (``development`` or
        ``review``, #378 item 6), or None for all.
    :param include_events: When True, embed each session's linked events.
    :return: A list of session dicts ordered by start time.
    """
    # The shared range_bounds keeps the query window and the upload path's
    # orphan-sweep window on one inclusive-date semantic (see #354).
    lo, hi = range_bounds(args.start_date, args.end_date)
    sessions = derive_windows(
        services.events,
        services.jobs,
        lo,
        hi,
        gap_secs=services.config.session_gap_secs,
        task_id=args.task_id,
        repo=args.repo,
    )
    # A session's strategy is a per-group label the derivation computes
    # (development-family vs review-family), so the strategy filter is applied
    # over the derived windows rather than short-circuiting the whole query.
    return [
        _render(services, session, args.include_events)
        for session in sessions
        if args.strategy_name is None or session.strategy_name == args.strategy_name
    ]
