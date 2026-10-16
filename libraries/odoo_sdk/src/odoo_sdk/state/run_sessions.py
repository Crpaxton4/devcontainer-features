"""The run record as a sessionization source (#1065).

One completed job is one bounded interval of work on exactly one task, taken
from the dispatcher's own clock. That is a measurement, so it needs no
attribution. :func:`merge_runs` folds those intervals into the windows the
event-store CTE already derived, with the same gap rule and per task.

**Sessions are grouped by time and durations are never summed.** A run and the
events inside it, or a run and the events next to it within the gap, collapse
into one session. Two sources that describe the same work therefore cannot bill
it twice, which is what makes it safe to run both at once and compare them.

Session identity (the upload idempotency key): a window that holds any run is
keyed on the ``session_id`` of its earliest run (by start, then id), see
:func:`odoo_sdk.tracking.models.session_key`. Events joining or leaving the window
cannot move that key. A window made only of events keeps the smallest event id.
A window's ``id`` stays an int for existing consumers: the smallest event id, or
``-started_us`` of the earliest run when there are no events.

A run that ended ``FAILED`` marks its window ``has_failed``. The time is real and
is kept, but whether it bills is for a human at upload, so it is marked and never
dropped.
"""

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
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


def read_runs(path: Path, start_us: int, end_us: int, *, expected: bool) -> list:
    """Return the runs in ``[start_us, end_us)`` from ``path``, strictly read-only.

    The connection is opened ``mode=ro``: it never creates the file, never sets
    the journal mode and never runs a migration. The window rule is not repeated
    here, it is :meth:`RunStore.iter_runs`, run over that read-only connection.

    * Absent file: ``[]`` when the path was merely the default location (no
      dispatcher has run), but :class:`FileNotFoundError` when the caller named
      the file (``expected=True``), so a wrong path is said plainly.
    * Zero-byte file: ``[]``. It holds no schema and is left untouched.
    * Corrupt, locked or schema-less file: the sqlite error propagates. Falling
      back to the event store silently would bill a different answer.
    """
    from .runs_store import RunStore  # lazy: runs_store imports this package's db

    if not path.exists():
        if expected:
            raise FileNotFoundError(f"run record not found: {path}")
        return []
    if path.stat().st_size == 0:
        return []
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        store = RunStore.__new__(RunStore)
        store.path, store._lock, store._conn = path, threading.RLock(), conn
        return store.iter_runs(start_us=start_us, end_us=end_us)
    finally:
        conn.close()


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
    run_key: Optional[tuple[int, str]]  # (started_us, session_id) of the earliest run
    failed: bool


def _span_of_window(win: SessionWindow) -> _Span:
    return _Span(
        win.task_id,
        as_utc(win.started_at),
        as_utc(win.ended_at),
        win.repo,
        win.pr_num,
        win.strategy_name == "development",
        tuple(win.event_ids),
        None,
        False,
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
        (run.started_us, run.session_id),
        run.outcome == "FAILED",  # RunOutcome is a str enum,
    )


def _join(a: _Span, b: _Span) -> _Span:
    run_keys = [k for k in (a.run_key, b.run_key) if k is not None]
    return _Span(
        a.task_id,
        min(a.started, b.started),
        max(a.ended, b.ended),
        # Legacy rule: COALESCE(MAX(NULLIF(repo, '')), ...), order independent.
        max(filter(None, (a.repo, b.repo)), default=""),
        max(a.pr_num, b.pr_num),
        a.is_dev or b.is_dev,
        tuple(sorted(set(a.event_ids) | set(b.event_ids))),
        min(run_keys) if run_keys else None,
        a.failed or b.failed,
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
        key_id = -(span.run_key[0] if span.run_key else 0)
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


__all__ = ["from_us", "merge_runs", "read_runs", "to_us"]
