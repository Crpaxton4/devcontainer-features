"""Builtin command exposing the gap-sweep utilisation analysis (read-only).

Session identity is fixed by the configured gap and sessions are derived from the
``events`` timeseries at query time (see
:meth:`odoo_sdk.state.LocalStateClient.derive_sessions_overlapping`). The gap
sweep here is a decoupled, analysis-only reporting concern: it explores what
utilisation *would* result at each candidate gap to recommend one, but it never
materializes or mutates any session state, so it cannot shift the identity of
detected sessions. It reports; it does not write.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Optional

from odoo_sdk.adapters import load_raw_events
from odoo_sdk.sessionization import SessionizationConfig, transform

from ..command import Command
from ._registration import builtin_command


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
        "window_gap_secs",
        "min_task_minutes",
        "billing_step_mins",
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


@builtin_command
class OptimizeSessionsCommand(Command):
    """Run the read-only gap-sweep utilisation analysis over stored events.

    Reads raw events from the local ``events`` table and runs the gap sweep and
    utilisation optimizer purely to *report* the best-utilisation gap. It never
    writes any session state: session identity is owned by the fixed configured
    gap and derived from events at query time, so this analysis is decoupled
    from it and cannot mutate it. All manual knobs (date range, sweep bounds,
    scoring coefficients) are keyword arguments so every manual interaction is a
    command argument rather than an in-script flag.
    """

    _name = "optimize_sessions"
    _description = (
        "Analyze stored events by sweeping inactivity gaps and reporting the "
        "best-utilisation gap. Read-only: it never mutates session identity."
    )

    def execute(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        **overrides: Any,
    ) -> dict[str, Any]:
        """Analyze sessions and return the sweep summary (no persistence).

        :param start_date: Inclusive ISO start date (``YYYY-MM-DD``).
        :param end_date: Inclusive ISO end date (``YYYY-MM-DD``).
        :param overrides: Optional sweep/scoring hyperparameter overrides.
        :return: A summary dict with the best gap, score, totals, and counts.
        """
        config = _build_config(start_date, end_date, overrides)
        events = load_raw_events(self.state, config.range_start, config.range_end)
        result = transform(events, config)
        return {
            "best_gap_mins": result.sweep.best_gap,
            "best_score": result.sweep.best_score,
            "best_total_secs": result.sweep.best_total,
            "num_days": result.sweep.num_days,
            "event_count": len(events),
            "entry_count": len(result.best_gap_entries),
        }
