"""Pure Transform phase for the sessionization ETL.

Turns normalized :class:`RawEvent` rows into computed :class:`TimeEntry` rows,
sweeps candidate inactivity gaps, and scores each by average daily utilisation
to pick the best gap. No I/O is performed.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from .config import SessionizationConfig
from .models import EventType, RawEvent, SweepResults, TimeEntry, TransformResult
from .scoring import score_day
from .windows import billable_seconds, compute_windows

# Sources that DO NOT participate in gap-based sessionization. ``merge`` is a
# point-in-time release marker (audit-only), excluded from windowed billing
# exactly as the SQL derivation excludes it (issue #404 / #378 item 6). Every
# other event type — the development family and the review family — windows
# uniformly; there is no per-event fixed-duration strategy anymore.
_NON_SESSION_TYPES = frozenset({EventType.MERGE})

# Development-family event types: a window holding ANY of these is labeled
# "Development" (development wins a mixed task's label); a window of purely
# review-family events (``REVIEW``) is labeled "Review". Mirrors the SQL
# derivation's ``has_dev`` decision so the two engines agree on the category.
_DEVELOPMENT_TYPES = frozenset(
    {
        EventType.COMMIT,
        EventType.AGENT,
        EventType.CLAUDE_HOOK,
        EventType.CHATTER,
        EventType.CALENDAR,
        EventType.EMAIL,
    }
)


def _session_label(events: list[RawEvent]) -> tuple[str, str]:
    """Return the ``(strategy_name, category)`` label for a window's events."""
    if any(event.event_type in _DEVELOPMENT_TYPES for event in events):
        return "development", "Development"
    return "review", "Review"


def _window_entry(
    task_id: str,
    window_events: list[RawEvent],
    start: datetime,
    end_raw: datetime,
    config: SessionizationConfig,
) -> TimeEntry:
    """Build one billed :class:`TimeEntry` from a raw window and its events."""
    # A window always holds at least the event(s) that anchor its bounds.
    strategy_name, category = _session_label(window_events)
    # Display-only metadata, mirroring the SQL derivation's
    # ``COALESCE(MAX(NULLIF(repo, '')), :agentless)``: the greatest real label
    # wins, and a purely repo-less window yields the absent repo
    # (:data:`~odoo_sdk.state.db.AGENTLESS_REPO`, i.e. ``""``) that path also
    # returns — the two derivations agree on the same input (#508).
    repo = max((event.repo for event in window_events if event.repo), default="")
    billed = billable_seconds((end_raw - start).total_seconds(), config)
    return TimeEntry(
        task_id=task_id,
        repo=repo,
        pr_num=max(event.pr_num for event in window_events),
        start=start,
        end=start + timedelta(seconds=billed),
        label=repo,
        branch=", ".join(
            sorted({event.branch for event in window_events if event.branch})
        ),
        source_events=window_events,
        strategy_name=strategy_name,
        strategy_category=category,
        activity_type=window_events[0].event_type.name,
    )


def build_window_entries(
    events: list[RawEvent], gap_secs: int, config: SessionizationConfig
) -> list[TimeEntry]:
    """Window events into billed entries by the single SQL-parity algorithm.

    Every session-source event (development + review families; ``merge`` is
    excluded, matching ``derive_sessions_overlapping``) is fanned out over its
    task ids, grouped by task alone (repo is display metadata, not a partition
    key — #352), and partitioned into gap-separated windows. Each window bills its
    raw wall-clock span through :func:`billable_seconds`, so the entries reproduce
    the SQL derivation's grouping and the upload path's billed hours.
    """
    by_task: dict[str, list[RawEvent]] = {}
    for event in events:
        if event.event_type in _NON_SESSION_TYPES:
            continue
        for task_id in event.task_ids or ["UNKNOWN"]:
            by_task.setdefault(task_id, []).append(event)
    entries: list[TimeEntry] = []
    for task_id, task_events in by_task.items():
        ordered = sorted(task_events, key=lambda event: event.timestamp)
        for start, end_raw in compute_windows(
            [event.timestamp for event in ordered], gap_secs
        ):
            window_events = [
                event for event in ordered if start <= event.timestamp <= end_raw
            ]
            entries.append(
                _window_entry(task_id, window_events, start, end_raw, config)
            )
    return sorted(
        entries, key=lambda entry: (entry.start, entry.repo, entry.task_id)
    )


def billable_events(events: list[RawEvent]) -> list[RawEvent]:
    """Return events with resolved task IDs only.

    The canonical task-attribution predicate is the SQL in ``state/db.py``'s
    ``derive_sessions_overlapping`` (``json_array_length(events.task_ids) > 0``,
    with ``COALESCE(NULLIF(value, ''), 'UNKNOWN')`` bucketing) — that production
    derivation is the single source of truth for whether an event carries a task.
    This diagnostic gap-sweep filter shares that "non-empty ``task_ids``" rule and
    additionally drops events whose task set is unresolved (contains ``UNKNOWN``),
    because the sweep scores resolved-task utilisation and must not let an
    ``UNKNOWN`` bucket skew gap selection; the SQL path instead keeps such rows
    under an ``UNKNOWN`` ``task_key`` for the full audit. Both the shared predicate
    and this intentional divergence are pinned by
    ``tests/test_sessionization/test_parity.py``.
    """
    return [
        event
        for event in events
        if event.task_ids and "UNKNOWN" not in event.task_ids
    ]


def target_day_totals(
    entries: list[TimeEntry], config: SessionizationConfig
) -> dict[date, float]:
    """Return target-date totals, splitting windows at day-bucket-zone midnight."""
    tz = config.day_bucket_tz
    totals = {target_date: 0.0 for target_date in config.target_dates}
    for entry in entries:
        cursor = entry.start.astimezone(tz)
        end = entry.end.astimezone(tz)
        while cursor < end:
            next_day = cursor.date() + timedelta(days=1)
            midnight = datetime.combine(next_day, time.min, tzinfo=tz)
            segment_end = min(end, midnight)
            if cursor.date() in totals:
                totals[cursor.date()] += (segment_end - cursor).total_seconds()
            cursor = segment_end
    return totals


def _score_entries_by_target_day(
    entries: list[TimeEntry], config: SessionizationConfig
) -> float:
    """Return the mean score after scoring each target day independently."""
    day_totals = target_day_totals(entries, config)
    if not day_totals:
        return score_day(0.0, config)
    return sum(score_day(total, config) for total in day_totals.values()) / len(
        day_totals
    )


def _sweep_gap(
    events: list[RawEvent], gap_mins: int, config: SessionizationConfig
) -> tuple[dict[str, float], float, float]:
    """Return task totals, combined total, and daily score for one gap."""
    task_sums: dict[str, float] = {}
    entries = build_window_entries(events, gap_mins * 60, config)
    for entry in entries:
        dur = (entry.end - entry.start).total_seconds()
        task_sums[entry.task_id] = task_sums.get(entry.task_id, 0.0) + dur
    return (
        task_sums,
        sum(task_sums.values()),
        _score_entries_by_target_day(entries, config),
    )


def _sweep_all_gaps(
    events: list[RawEvent], gap_vals: list[int], config: SessionizationConfig
) -> tuple[list[dict[str, float]], list[float], list[float], set[str]]:
    """Compute task sums, combined totals, and scores for every gap value."""
    all_task_sums: list[dict[str, float]] = []
    combined: list[float] = []
    scores: list[float] = []
    all_task_ids: set[str] = set()
    for gap in gap_vals:
        sums, total, score = _sweep_gap(events, gap, config)
        all_task_ids.update(sums.keys())
        all_task_sums.append(sums)
        combined.append(total)
        scores.append(score)
    return all_task_sums, combined, scores, all_task_ids


def _find_best_gap(gap_vals: list[int], scores: list[float]) -> int:
    """Return the index of the best gap value.

    Scores are computed per target day, so gap selection uses the score alone
    (with a tie-break toward the smaller gap) and never the combined total,
    which would reintroduce cross-day skew.
    """
    return max(range(len(scores)), key=lambda i: (scores[i], -gap_vals[i]))


def _build_per_task_matrix(
    all_task_sums: list[dict[str, float]], all_task_ids: set[str]
) -> dict[str, list[float]]:
    """Build the per-task totals matrix from cached sweep results."""
    return {
        tid: [sums.get(tid, 0.0) for sums in all_task_sums]
        for tid in sorted(all_task_ids)
    }


def _sweep_gap_values(config: SessionizationConfig) -> list[int]:
    """Return the ordered list of gap values (minutes) to sweep."""
    return list(
        range(
            config.sweep_min_gap_mins,
            config.sweep_max_gap_mins + 1,
            config.sweep_step_mins,
        )
    )


def sweep(events: list[RawEvent], config: SessionizationConfig) -> SweepResults:
    """Test all gap values; find the one with the best utilisation score."""
    gap_vals = _sweep_gap_values(config)
    all_task_sums, combined, scores, all_task_ids = _sweep_all_gaps(
        events, gap_vals, config
    )
    per_task = _build_per_task_matrix(all_task_sums, all_task_ids)
    obs_mean = sum(combined) / len(combined) if combined else 0.0
    best_idx = _find_best_gap(gap_vals, scores)
    return SweepResults(
        gap_vals=gap_vals,
        combined=combined,
        per_task=per_task,
        scores=scores,
        obs_mean=obs_mean,
        best_gap=gap_vals[best_idx],
        best_total=combined[best_idx],
        best_score=scores[best_idx],
        num_days=config.num_days,
    )


def transform(
    events: list[RawEvent], config: SessionizationConfig
) -> TransformResult:
    """Compute all time entries and sweep results from raw events."""
    events_for_billing = billable_events(events)
    window_entries = build_window_entries(
        events_for_billing, config.session_gap_secs, config
    )
    sweep_results = sweep(events_for_billing, config)
    best_gap_entries = build_window_entries(
        events_for_billing, sweep_results.best_gap * 60, config
    )
    return TransformResult(
        all_entries=window_entries,
        best_gap_entries=best_gap_entries,
        sweep=sweep_results,
        raw_events=events,
    )
