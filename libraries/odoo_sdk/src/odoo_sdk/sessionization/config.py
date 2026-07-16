"""Pure configuration for the sessionization ETL.

``SessionizationConfig`` is a plain dataclass carrying every hyperparameter the
Transform phase needs: the date range, window/billing sizes, the gap sweep
bounds, and the utilisation-scoring coefficients. It performs no I/O and reads
no module-level globals; callers construct it explicitly (e.g. from a CLI or a
state adapter).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, tzinfo

from .models import (
    resolve_day_bucket_tz,
    resolve_min_session_hours,
    resolve_round_session_hours,
    resolve_session_gap_secs,
)


@dataclass
class SessionizationConfig:
    """Hyperparameters for a single sessionization run.

    All fields have defaults so a bare ``SessionizationConfig()`` is valid; the
    CLI and adapters override individual fields.

    The window gap and the billing policy (floor + rounding step) default from the
    single first-class config (``LocalConfig``, issue #404), so the diagnostic
    gap-sweep and the export re-window and round on exactly the knobs the
    SQL-derived billing/upload path uses — no parallel minute-denominated
    ``min_task_minutes`` / ``billing_step_mins`` vocabulary survives.
    """

    # Date range (inclusive).
    start_date: date = field(default_factory=lambda: date(2026, 6, 1))
    end_date: date = field(default_factory=lambda: date(2026, 6, 2))
    target_excluded_dates: set[date] = field(default_factory=set)

    # Fixed session gap + billing policy (resolved from LocalConfig; shared with
    # the SQL derivation and the upload path so numbers cannot diverge).
    session_gap_secs: int = field(default_factory=resolve_session_gap_secs)
    min_session_hours: float = field(default_factory=resolve_min_session_hours)
    round_session_hours: float = field(default_factory=resolve_round_session_hours)

    # Gap sweep bounds (minutes).
    sweep_min_gap_mins: int = 30
    sweep_max_gap_mins: int = 240
    sweep_step_mins: int = 5

    # Utilisation-scoring coefficients.
    b_low: float = 8.0  # lower utilisation boundary (h/day)
    b_high: float = 12.0  # upper (dishonesty) boundary (h/day)
    s_low: float = 0.0  # anchor score at b_low
    s_high: float = 1.0  # anchor score at b_high
    k1: float = 0.5  # underutilisation exp rate
    k2: float = 1.0  # optimal-zone growth rate
    k3: float = 2.0  # dishonesty exp rate

    # Odoo CSV rendering identifiers.
    odoo_employee_id: int = 49
    odoo_uom_id: int = 6
    odoo_company_id: int = 1

    # Day-bucketing timezone (issue #378 item 11). Resolved from the standard
    # config resolver by default (``[behavior] day_bucket_tz``, default US
    # Central); the pure Transform phase reads the zone from here so bucketing is
    # config-driven without this module touching the state layer at import.
    day_bucket_tz: tzinfo = field(default_factory=resolve_day_bucket_tz)

    def __post_init__(self) -> None:
        """Enforce the monotonicity precondition on the sweep minimum gap.

        Requiring ``sweep_min_gap_mins >= 2 * min_session_hours`` (in minutes)
        guarantees total billed time is non-decreasing as the gap grows: any two
        sessions that merge are at least ``2 * floor`` apart, so the merged
        elapsed always exceeds the sum of the two floored originals.
        """
        min_valid = 2 * self.min_session_hours * 60
        if self.sweep_min_gap_mins < min_valid:
            raise ValueError(
                "sweep_min_gap_mins must be at least "
                f"2 * min_session_hours in minutes ({min_valid})"
            )

    @property
    def range_start(self) -> datetime:
        """Return midnight on ``start_date`` in the day-bucketing zone (tz-aware)."""
        return datetime.combine(self.start_date, time.min, tzinfo=self.day_bucket_tz)

    @property
    def range_end(self) -> datetime:
        """Return midnight on the day after ``end_date`` (half-open, tz-aware)."""
        next_day = self.end_date + timedelta(days=1)
        return datetime.combine(next_day, time.min, tzinfo=self.day_bucket_tz)

    @property
    def num_days(self) -> int:
        """Return the count of non-excluded target days."""
        return len(self.target_dates)

    @property
    def target_dates(self) -> list[date]:
        """Return the ordered list of non-excluded days in the range."""
        span = (self.end_date - self.start_date).days + 1
        return [
            day
            for offset in range(span)
            if (day := self.start_date + timedelta(days=offset))
            not in self.target_excluded_dates
        ]

    def in_range(self, ts: datetime) -> bool:
        """Return True iff ``ts`` falls in ``[range_start, range_end)``."""
        return self.range_start <= ts < self.range_end
