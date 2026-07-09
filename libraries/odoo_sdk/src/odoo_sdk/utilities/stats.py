"""Pure statistics over queried sessions and their events.

This utility turns the plain result of the ``query_sessions`` command — a list
of session summary dicts, each optionally carrying its linked events — into
descriptive statistics: counts, averages, totals, utilization ratios, and
parallelization ratios. It performs no I/O and knows nothing of SQLite, Odoo, or
the terminal; it deals only in dicts and primitives so it is trivially testable
and reusable by any surface.

The session dict shape it consumes is the one ``QuerySessionsCommand`` emits:
``started_at`` / ``ended_at`` ISO-8601 strings, ``task_id``, ``repo``,
``strategy_name``, ``duration_secs``, and an optional ``events`` list whose items
carry ``source`` and ``timestamp``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Sequence

Session = Mapping[str, Any]
Event = Mapping[str, Any]

_SECONDS_PER_HOUR = 3600.0


@dataclass(frozen=True)
class SessionStats:
    """Descriptive statistics over a window of sessions and events."""

    total_events: int
    events_by_type: dict[str, int]
    session_count: int
    task_count: int
    session_hours: float
    span_hours: float
    events_per_day: float
    events_per_week: float
    target_utilization: float
    calendar_utilization: float
    overlap_ratio: float
    peak_concurrency: int
    mean_concurrency: float
    active_days: int = 0
    lane_count: int = 0
    tasks: list[str] = field(default_factory=list)


def _parse(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp string into a :class:`datetime`."""
    return datetime.fromisoformat(ts)


def _bounds(sessions: Sequence[Session]) -> tuple[datetime, datetime] | None:
    """Return the ``(earliest start, latest end)`` across sessions, or None."""
    if not sessions:
        return None
    starts = [_parse(s["started_at"]) for s in sessions]
    ends = [_parse(s["ended_at"]) for s in sessions]
    return min(starts), max(ends)


def _count_events(sessions: Sequence[Session]) -> tuple[int, dict[str, int]]:
    """Return the total event count and a per-source-type breakdown."""
    total = 0
    by_type: dict[str, int] = {}
    for session in sessions:
        for event in session.get("events", []) or []:
            total += 1
            source = str(event.get("source", "unknown"))
            by_type[source] = by_type.get(source, 0) + 1
    return total, by_type


def _active_days(sessions: Sequence[Session]) -> int:
    """Return the count of distinct calendar days a session started on."""
    return len({_parse(s["started_at"]).date() for s in sessions})


def _covered_seconds(sessions: Sequence[Session]) -> float:
    """Return the wall-clock seconds covered by the union of session intervals.

    Overlapping sessions are merged so simultaneous work is counted once. This
    is the denominator of the overlap parallelization ratio.
    """
    intervals = sorted(
        (_parse(s["started_at"]), _parse(s["ended_at"])) for s in sessions
    )
    covered = 0.0
    cur_start: datetime | None = None
    cur_end: datetime | None = None
    for start, end in intervals:
        if cur_end is None or start > cur_end:
            if cur_start is not None:
                covered += (cur_end - cur_start).total_seconds()
            cur_start, cur_end = start, end
        elif end > cur_end:
            cur_end = end
    if cur_start is not None and cur_end is not None:
        covered += (cur_end - cur_start).total_seconds()
    return covered


def _peak_concurrency(sessions: Sequence[Session]) -> int:
    """Return the maximum number of sessions simultaneously active.

    A sweep line over interval endpoints tracks how many intervals are open at
    once; ends are processed before starts at a shared instant so a session that
    ends exactly as another starts is not double-counted.
    """
    if not sessions:
        return 0
    events: list[tuple[datetime, int]] = []
    for session in sessions:
        events.append((_parse(session["started_at"]), 1))
        events.append((_parse(session["ended_at"]), -1))
    events.sort(key=lambda item: (item[0], item[1]))
    active = 0
    peak = 0
    for _, delta in events:
        active += delta
        peak = max(peak, active)
    return peak


def _session_seconds(sessions: Sequence[Session]) -> float:
    """Return the summed duration of all sessions in seconds."""
    return float(sum(s.get("duration_secs", 0.0) for s in sessions))


def _ratio(numerator: float, denominator: float) -> float:
    """Return ``numerator / denominator``, or 0.0 when the denominator is 0."""
    return numerator / denominator if denominator > 0 else 0.0


def compute_stats(
    sessions: Sequence[Session],
    *,
    target_hours_per_day: float = 8.0,
) -> SessionStats:
    """Compute descriptive statistics over queried ``sessions``.

    :param sessions: Session summary dicts as emitted by ``query_sessions``.
    :param target_hours_per_day: The billable target used for the target
        utilization ratio (session hours vs. target working hours).
    :return: A fully-computed :class:`SessionStats`.
    """
    total_events, events_by_type = _count_events(sessions)
    session_secs = _session_seconds(sessions)
    session_hours = session_secs / _SECONDS_PER_HOUR
    active_days = _active_days(sessions)
    tasks = sorted({str(s["task_id"]) for s in sessions})
    lanes = {
        (str(s["task_id"]), str(s.get("repo", "")), str(s.get("strategy_name", "")))
        for s in sessions
    }

    bounds = _bounds(sessions)
    span_hours = 0.0
    if bounds is not None:
        span_hours = (bounds[1] - bounds[0]).total_seconds() / _SECONDS_PER_HOUR

    target_secs = target_hours_per_day * _SECONDS_PER_HOUR * active_days
    covered_secs = _covered_seconds(sessions)

    events_per_day = _ratio(total_events, active_days)
    span_secs = span_hours * _SECONDS_PER_HOUR
    return SessionStats(
        total_events=total_events,
        events_by_type=events_by_type,
        session_count=len(sessions),
        task_count=len(tasks),
        session_hours=round(session_hours, 4),
        span_hours=round(span_hours, 4),
        events_per_day=round(events_per_day, 4),
        events_per_week=round(events_per_day * 7, 4),
        target_utilization=round(_ratio(session_secs, target_secs), 4),
        calendar_utilization=round(_ratio(session_secs, span_secs), 4),
        overlap_ratio=round(_ratio(session_secs, covered_secs), 4),
        peak_concurrency=_peak_concurrency(sessions),
        mean_concurrency=round(_ratio(session_secs, span_secs), 4),
        active_days=active_days,
        lane_count=len(lanes),
        tasks=tasks,
    )
