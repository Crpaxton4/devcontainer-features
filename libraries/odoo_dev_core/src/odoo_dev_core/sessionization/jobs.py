"""Dispatched job rows as a sessionization source.

One finished job is one bounded interval of work on exactly one task, taken from
the dispatcher's own clock. That is a measurement, so it needs no attribution.
:func:`sessions_from_jobs` turns job rows into one session window each.
:func:`merge_job_windows` folds those windows into the windows derived from
events, with the same gap rule and per task.

**Sessions are grouped by time and durations are never summed.** A job and the
events inside it, or a job and the events next to it within the gap, collapse
into one session. Two sources that describe the same work therefore cannot bill
it twice.

Session identity (the upload idempotency key): a window that holds any job is
keyed on the ``session_id`` of its earliest job (by start, then session id), see
:func:`odoo_dev_data.events.session_key`. Events joining or leaving the window
cannot move that key. A window made only of events keeps the smallest event id.
A window's ``id`` stays an int: the smallest event id, or ``-started_us`` of the
earliest job when there are no events.

A job that ended ``FAILED`` or dead-lettered marks its window ``has_failed``. The
time is real and is kept. Whether it bills is for a human at upload, so it is
marked and never dropped.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Protocol, Sequence

from odoo_dev_core.ports import JobsPort, StateStore
from odoo_dev_data.events import AGENTLESS_REPO, SessionWindow

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_US = timedelta(microseconds=1)
_MIN_MARGIN_SECS = 86_400


class JobRow(Protocol):
    """The columns of a finished job that sessionization reads."""

    @property
    def task_id(self) -> str: ...

    @property
    def repo(self) -> str: ...

    @property
    def pr_num(self) -> int: ...

    @property
    def session_id(self) -> str: ...

    @property
    def state(self) -> str: ...

    @property
    def outcome(self) -> str: ...

    @property
    def started_us(self) -> int | None: ...

    @property
    def ended_us(self) -> int | None: ...


def to_us(ts: datetime) -> int:
    """Return ``ts`` as integer microseconds since the epoch, UTC."""
    aware = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts
    return (aware.astimezone(timezone.utc) - _EPOCH) // _US


def from_us(us: int) -> datetime:
    """Return the UTC datetime of integer microseconds since the epoch."""
    return _EPOCH + timedelta(microseconds=us)


def sessions_from_jobs(jobs: Sequence[JobRow]) -> list[SessionWindow]:
    """Return one session window per finished job that names a task.

    The window bounds are the job's microsecond marks. The window ``id`` is
    ``-started_us`` and ``run_session_id`` is the job's session id, so
    :func:`~odoo_dev_data.events.session_key` yields ``task|run:session``.
    ``has_failed`` is set when the job ended ``FAILED`` or dead-lettered. A job
    that never started or has no task id has no interval to bill and is skipped.
    """
    windows: list[SessionWindow] = []
    for job in jobs:
        if job.started_us is None or not job.task_id:
            continue
        ended_us = job.started_us if job.ended_us is None else job.ended_us
        windows.append(
            SessionWindow(
                id=-job.started_us,
                task_id=str(job.task_id),
                repo=job.repo or AGENTLESS_REPO,
                started_at=from_us(job.started_us),
                ended_at=from_us(ended_us),
                strategy_name="development",
                category="Development",
                pr_num=job.pr_num,
                run_session_id=job.session_id,
                has_failed=job.outcome == "FAILED" or job.state == "DEAD_LETTER",
            )
        )
    return windows


@dataclass(frozen=True)
class _Span:
    """One interval of work on one task, from either source."""

    task_id: str
    started: datetime
    ended: datetime
    repo: str
    pr_num: int
    is_dev: bool
    event_ids: tuple[int, ...]
    run_key: Optional[tuple[int, str]]  # (started_us, session_id) of the earliest job
    failed: bool


def _span_of_event_window(win: SessionWindow) -> _Span:
    return _Span(
        win.task_id,
        win.started_at,
        win.ended_at,
        win.repo,
        win.pr_num,
        win.strategy_name == "development",
        tuple(win.event_ids),
        None,
        False,
    )


def _span_of_job_window(win: SessionWindow) -> _Span:
    started_us = -(win.id or 0)
    return _Span(
        win.task_id,
        win.started_at,
        win.ended_at,
        win.repo,
        win.pr_num,
        True,
        (),
        (started_us, win.run_session_id),
        win.has_failed,
    )


def _join(a: _Span, b: _Span) -> _Span:
    run_keys = [k for k in (a.run_key, b.run_key) if k is not None]
    earliest = min(run_keys) if run_keys else None
    return _Span(
        a.task_id,
        min(a.started, b.started),
        max(a.ended, b.ended),
        # Same rule as the event derivation: the longest non-empty repo label,
        # independent of the order of the spans.
        max(filter(None, (a.repo, b.repo)), default=""),
        max(a.pr_num, b.pr_num),
        a.is_dev or b.is_dev,
        tuple(sorted(set(a.event_ids) | set(b.event_ids))),
        earliest,
        a.failed or b.failed,
    )


def _same_session(cur: _Span, nxt: _Span, gap_secs: int) -> bool:
    """Return whether ``nxt`` continues ``cur``: the event derivation's gap cut."""
    if nxt.task_id != cur.task_id:
        return False
    gap = (nxt.started - cur.ended).total_seconds()
    return int(gap + 0.5) <= gap_secs if gap > 0 else True


def _fold(spans: list[_Span], gap_secs: int) -> list[_Span]:
    """Merge spans of one task that are at most ``gap_secs`` apart or overlap."""
    folded: list[_Span] = []
    for span in sorted(spans, key=lambda s: (s.task_id, s.started)):
        if folded and _same_session(folded[-1], span, gap_secs):
            folded[-1] = _join(folded[-1], span)
        else:
            folded.append(span)
    return folded


def _window_of(span: _Span) -> SessionWindow:
    # Events win the id; a window of jobs alone keeps ``-started_us`` of its
    # earliest job, which is the id ``sessions_from_jobs`` gave that job.
    if span.event_ids:
        key_id = span.event_ids[0]
    else:
        key_id = -span.run_key[0] if span.run_key else None
    strategy, category = (
        ("development", "Development") if span.is_dev else ("review", "Review")
    )
    return SessionWindow(
        id=key_id,
        task_id=span.task_id,
        repo=span.repo or AGENTLESS_REPO,
        started_at=span.started,
        ended_at=span.ended,
        strategy_name=strategy,
        category=category,
        pr_num=span.pr_num,
        event_ids=span.event_ids,
        run_session_id=span.run_key[1] if span.run_key else "",
        has_failed=span.failed,
    )


def merge_job_windows(
    windows: list[SessionWindow],
    job_windows: list[SessionWindow],
    *,
    gap_secs: int,
) -> list[SessionWindow]:
    """Fold ``job_windows`` into the event-derived ``windows``, ordered by start.

    Windows of one task that overlap or sit within ``gap_secs`` of each other
    become one window spanning both. Event ids are united. The earliest job's
    session id keys the merged window, and ``has_failed`` sticks.
    """
    spans = [_span_of_event_window(w) for w in windows]
    spans += [_span_of_job_window(w) for w in job_windows]
    merged = [_window_of(s) for s in _fold(spans, gap_secs)]
    return sorted(merged, key=lambda w: w.started_at)


def derive_windows(
    state: StateStore,
    jobs: JobsPort | None,
    start: datetime,
    end: datetime,
    *,
    gap_secs: int,
    task_id: Optional[str] = None,
    repo: Optional[str] = None,
) -> list[SessionWindow]:
    """Derive whole sessions overlapping ``[start, end]`` from events and jobs.

    With no ``jobs`` port the result is the event derivation alone. Otherwise
    the finished jobs near the window (widened by the event derivation margin,
    ``max(gap_secs, 1 day)``) are folded in. The ``repo`` filter applies to the
    merged windows. Results overlap ``[start, end]`` and are ordered by start.
    """
    windows = state.derive_sessions_overlapping(
        start, end, gap_secs=gap_secs, task_id=task_id
    )
    if jobs is not None:
        margin = timedelta(seconds=max(gap_secs, _MIN_MARGIN_SECS))
        job_windows = sessions_from_jobs(
            jobs.list_finished(
                start_us=_edge_us(start, -margin), end_us=_edge_us(end, margin)
            )
        )
        if task_id is not None:
            job_windows = [w for w in job_windows if w.task_id == task_id]
        windows = merge_job_windows(windows, job_windows, gap_secs=gap_secs)
        lo, hi = _aware(start), _aware(end)
        windows = [w for w in windows if w.started_at <= hi and w.ended_at >= lo]
    if repo is not None:
        windows = [w for w in windows if w.repo == repo]
    return windows


def _aware(ts: datetime) -> datetime:
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts


def _edge_us(ts: datetime, delta: timedelta) -> int | None:
    """Return ``ts + delta`` in microseconds, or None when it is unbounded."""
    try:
        return to_us(ts + delta)
    except OverflowError:
        return None
