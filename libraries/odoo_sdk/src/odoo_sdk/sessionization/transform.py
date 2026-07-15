"""Pure Transform phase for the sessionization ETL.

Turns normalized :class:`RawEvent` rows into computed :class:`TimeEntry` rows,
sweeps candidate inactivity gaps, and scores each by average daily utilisation
to pick the best gap. No I/O is performed.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from .config import SessionizationConfig
from .models import RawEvent, SweepResults, TimeEntry, TransformResult
from .scoring import score_day
from .strategies import make_sessionization_context


def build_window_entries(
    events: list[RawEvent], gap_secs: int, config: SessionizationConfig
) -> list[TimeEntry]:
    """Build entries through the sessionization strategy context."""
    context = make_sessionization_context(config.session_strategy_configs)
    return context.build_entries(events, gap_secs, config)


def billable_events(events: list[RawEvent]) -> list[RawEvent]:
    """Return events with resolved task IDs only."""
    return [
        event
        for event in events
        if event.task_ids and "UNKNOWN" not in event.task_ids
    ]


def _target_day_totals(
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
            midnight = datetime(
                next_day.year, next_day.month, next_day.day, tzinfo=tz
            )
            segment_end = min(end, midnight)
            if cursor.date() in totals:
                totals[cursor.date()] += (segment_end - cursor).total_seconds()
            cursor = segment_end
    return totals


def target_day_totals(
    entries: list[TimeEntry], config: SessionizationConfig
) -> dict[date, float]:
    """Public wrapper over :func:`_target_day_totals` for renderers/adapters."""
    return _target_day_totals(entries, config)


def _score_entries_by_target_day(
    entries: list[TimeEntry], config: SessionizationConfig
) -> float:
    """Return the mean score after scoring each target day independently."""
    day_totals = _target_day_totals(entries, config)
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
    per_task: dict[str, list[float]] = {tid: [] for tid in sorted(all_task_ids)}
    for sums in all_task_sums:
        for tid in sorted(all_task_ids):
            per_task[tid].append(sums.get(tid, 0.0))
    return per_task


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
        events_for_billing, config.window_gap_secs, config
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
