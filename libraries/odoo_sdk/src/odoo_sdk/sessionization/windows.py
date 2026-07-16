"""Pure window computation for the sessionization ETL.

Partitions sorted timestamps into gap-separated sessions and bills a session's
raw wall-clock span with the single upload billing rule (half-up rounding to the
configured step, then a per-session minimum floor).

This is the only in-Python re-windowing that survives issue #404: the SQL CTE
(:meth:`odoo_sdk.state.LocalStateClient.derive_sessions_overlapping`) is the live
billing algorithm, and :func:`compute_windows` exists solely so the diagnostic
gap-sweep can re-window across a grid of candidate gaps. A parity test pins this
function to the SQL derivation at the production gap so the two never drift.
"""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - hints only, avoids a config import cycle
    from .config import SessionizationConfig


def _round_half_up_to_step(hours: float, step: float) -> float:
    """Round ``hours`` to the nearest ``step`` multiple, ties going up.

    Deliberately identical to the upload path's ``_round_to_step`` (issue #355):
    half-up (not banker's) rounding on the :class:`~decimal.Decimal` string forms
    so a diagnostic or exported figure is always exactly what an upload would
    bill. A ``step`` of ``0`` (or negative) disables rounding.
    """
    if step <= 0:
        return hours
    quotient = (Decimal(str(hours)) / Decimal(str(step))).to_integral_value(
        rounding=ROUND_HALF_UP
    )
    return float(quotient * Decimal(str(step)))


def billable_seconds(raw_secs: float, config: "SessionizationConfig") -> float:
    """Return the billed duration (seconds) for one session's raw span.

    Applies the shared billing policy — round the raw span (in hours) to the
    nearest ``round_session_hours`` step half-up, then raise it to the
    ``min_session_hours`` floor — and converts back to seconds. This mirrors
    ``odoo_sdk.billing.upload._billable_hours`` bit-for-bit, so a session's
    billed duration here equals the hours an upload writes for the same span.
    """
    raw_hours = raw_secs / 3600.0
    billed_hours = max(
        _round_half_up_to_step(raw_hours, config.round_session_hours),
        config.min_session_hours,
    )
    return billed_hours * 3600.0


def compute_windows(
    timestamps: list[datetime], gap_secs: int
) -> list[tuple[datetime, datetime]]:
    """Partition sorted timestamps into raw sessions separated by gaps > gap_secs.

    Each returned window carries its *raw* wall-clock bounds (first and last event
    timestamp) — no billing rounding is applied to the bounds, so the grouping is
    directly comparable to the SQL derivation's ``marked``/``numbered`` session
    grouping. Billing is a separate concern: see :func:`billable_seconds`.
    """
    if not timestamps:
        return []
    sorted_ts = sorted(timestamps)
    windows: list[tuple[datetime, datetime]] = []
    win_start = sorted_ts[0]
    for i in range(1, len(sorted_ts)):
        gap = (sorted_ts[i] - sorted_ts[i - 1]).total_seconds()
        if gap > gap_secs:
            windows.append((win_start, sorted_ts[i - 1]))
            win_start = sorted_ts[i]
    windows.append((win_start, sorted_ts[-1]))
    return windows
