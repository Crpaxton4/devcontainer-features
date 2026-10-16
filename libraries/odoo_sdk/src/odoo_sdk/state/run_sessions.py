"""The run record as a sessionization source (#1065).

One completed job is one bounded interval of work on exactly one task, taken
from the dispatcher's own clock. That is a measurement, so it needs no
attribution. :func:`merge_runs` folds those intervals into the windows the
event-store CTE already derived, with the same gap rule and per task.

**Sessions are grouped by time and durations are never summed.** A run and the
events inside it, or a run and the events next to it within the gap, collapse
into one session. Two sources that describe the same work therefore cannot bill
it twice, which is what makes it safe to run both at once and compare them.

Session identity (the upload idempotency key is ``task|id``): a window that
holds any event keeps the smallest event id, so a key an old upload already used
stays valid. A window made only of runs takes ``-started_us`` of its earliest
run. That is negative, so it cannot collide with an event id, and a run row is
write-once, so it is stable.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from odoo_sdk._utils import as_utc

from .models import AGENTLESS_REPO, SessionWindow

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_US = timedelta(microseconds=1)


def to_us(ts: datetime) -> int:
    """Return ``ts`` as integer microseconds since the epoch, UTC."""
    return (as_utc(ts) - _EPOCH) // _US


def from_us(us: int) -> datetime:
    """Return the UTC datetime of integer microseconds since the epoch."""
    return _EPOCH + timedelta(microseconds=us)


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
    run_us: Optional[int]  # earliest run start in the span, None when events only


def _span_of_window(win: SessionWindow) -> _Span:
    return _Span(
        win.task_id,
        win.started_at,
        win.ended_at,
        win.repo,
        win.pr_num,
        win.strategy_name == "development",
        tuple(win.event_ids),
        None,
    )


def _span_of_run(run) -> _Span:
    return _Span(
        run.task_id,
        from_us(run.started_us),
        from_us(run.ended_us),
        run.repo,
        run.pr_num,
        True,
        (),
        run.started_us,
    )


def _join(a: _Span, b: _Span) -> _Span:
    run_starts = [u for u in (a.run_us, b.run_us) if u is not None]
    return _Span(
        a.task_id,
        min(a.started, b.started),
        max(a.ended, b.ended),
        a.repo or b.repo,
        max(a.pr_num, b.pr_num),
        a.is_dev or b.is_dev,
        tuple(sorted(set(a.event_ids) | set(b.event_ids))),
        min(run_starts) if run_starts else None,
    )


def _same_session(cur: _Span, nxt: _Span, gap_secs: int) -> bool:
    """Return whether ``nxt`` continues ``cur``: the CTE's ``ROUND(gap) > gap_secs`` cut."""
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
    if span.event_ids:
        key_id = span.event_ids[0]
    else:
        key_id = -(span.run_us or 0)
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
    )


def merge_runs(
    windows: list[SessionWindow],
    runs: list,
    *,
    gap_secs: int,
    start: datetime,
    end: datetime,
    repo: Optional[str] = None,
) -> list[SessionWindow]:
    """Fold ``runs`` into the event-derived ``windows`` and return the sessions.

    ``windows`` are whole event sessions (any repo, the task filter already
    applied). ``runs`` are :class:`~odoo_sdk.state.runs_store.RunRecord` rows
    for the same task scope. The result keeps only sessions that overlap
    ``[start, end]`` (inclusive, as the CTE's ``HAVING`` does), is filtered by
    the display ``repo`` after the merge, and is ordered by start time.
    """
    spans = [_span_of_window(w) for w in windows] + [_span_of_run(r) for r in runs]
    merged = [_window_of(s) for s in _fold(spans, gap_secs)]
    kept = [
        w
        for w in merged
        if w.started_at <= as_utc(end)
        and w.ended_at >= as_utc(start)
        and (repo is None or w.repo == repo)
    ]
    return sorted(kept, key=lambda w: w.started_at)


__all__ = ["from_us", "merge_runs", "to_us"]
