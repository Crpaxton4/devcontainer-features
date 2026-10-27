"""Builtin command exposing the gap-sweep utilisation analysis (read-only).

Session identity is fixed by the configured gap and sessions are derived from the
``events`` timeseries at query time (see
:meth:`odoo_dev_data.events.EventStore.derive_sessions_overlapping`). The gap
sweep here is a decoupled, analysis-only reporting concern: it explores what
utilisation *would* result at each candidate gap to recommend one, but it never
materializes or mutates any session state, so it cannot shift the identity of
detected sessions. It reports; it does not write.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Optional

from odoo_dev_core.sessionization.persistence import load_raw_events
from odoo_dev_core.sessionization import SessionizationConfig, transform

from ..ports import Services
from ..registry import command


def _parse_date(value: Optional[str]) -> Optional[date]:
    """Parse an ISO ``YYYY-MM-DD`` string into a :class:`date`, or None."""
    return date.fromisoformat(value) if value else None


def _build_config(
    start_date: Optional[str],
    end_date: Optional[str],
    overrides: dict[str, Any],
) -> SessionizationConfig:
    """Build a :class:`SessionizationConfig` from CLI-style knobs.

    Only recognised, non-None override fields are applied so callers may pass a
    sparse set of tuning knobs (sweep bounds, scoring coefficients, etc.).
    """
    values: dict[str, Any] = {}
    start = _parse_date(start_date)
    end = _parse_date(end_date)
    if start is not None:
        values["start_date"] = start
    if end is not None:
        values["end_date"] = end
    allowed = {
        "session_gap_secs",
        "min_session_hours",
        "round_session_hours",
        "sweep_min_gap_mins",
        "sweep_max_gap_mins",
        "sweep_step_mins",
        "b_low",
        "b_high",
        "s_low",
        "s_high",
        "k1",
        "k2",
        "k3",
    }
    values.update(
        {k: v for k, v in overrides.items() if k in allowed and v is not None}
    )
    return SessionizationConfig(**values)


@dataclass(frozen=True)
class Args:
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    session_gap_secs: Optional[int] = None
    min_session_hours: Optional[float] = None
    round_session_hours: Optional[float] = None
    sweep_min_gap_mins: Optional[int] = None
    sweep_max_gap_mins: Optional[int] = None
    sweep_step_mins: Optional[int] = None
    b_low: Optional[float] = None
    b_high: Optional[float] = None
    s_low: Optional[float] = None
    s_high: Optional[float] = None
    k1: Optional[float] = None
    k2: Optional[float] = None
    k3: Optional[float] = None


@command(
    "optimize_sessions",
    args=Args,
    description=(
        "Analyze stored events by sweeping inactivity gaps and reporting the "
        "best-utilisation gap. Read-only: it never mutates session identity."
    ),
)
def handle(services: Services, args: Args) -> dict[str, Any]:
    """Analyze sessions and return the sweep summary (no persistence).

    Reads raw events from the local ``events`` table and runs the gap sweep and
    utilisation optimizer purely to *report* the best-utilisation gap. It never
    writes any session state.

    :param start_date: Inclusive ISO start date (``YYYY-MM-DD``).
    :param end_date: Inclusive ISO end date (``YYYY-MM-DD``).
    :return: A summary dict with the best gap, score, totals, and counts.
    """
    config = _build_config(args.start_date, args.end_date, asdict(args))
    events = load_raw_events(
        services.events,
        config.range_start,
        config.range_end,
        exclude_synthetic=True,
    )
    result = transform(events, config)
    return {
        "best_gap_mins": result.sweep.best_gap,
        "best_score": result.sweep.best_score,
        "best_total_secs": result.sweep.best_total,
        "num_days": result.sweep.num_days,
        "event_count": len(events),
        "entry_count": len(result.best_gap_entries),
    }
