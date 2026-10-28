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
from typing import Any, Optional
from zoneinfo import ZoneInfo

from ..ports import SettingsView
from .models import DEFAULT_WINDOW_GAP_SECS

# Defaults of a bare ``SessionizationConfig()``. They equal the defaults of the
# resolved settings; ``from_settings`` replaces them with the user's values.
DEFAULT_MIN_SESSION_HOURS = 0.25
DEFAULT_ROUND_SESSION_HOURS = 0.05
DEFAULT_DAY_BUCKET_TZ = ZoneInfo("America/Chicago")


@dataclass
class SessionizationConfig:
    """Hyperparameters for a single sessionization run.

    All fields have defaults so a bare ``SessionizationConfig()`` is valid; the
    CLI and adapters override individual fields.

    The window gap, the billing policy (floor + rounding step) and the day
    bucket zone come from the resolved settings through
    :meth:`from_settings` (issue #404), so the diagnostic gap-sweep and the
    export re-window and round on exactly the knobs the SQL-derived
    billing/upload path uses. This class never loads a config itself.
    """

    # Date range (inclusive).
    start_date: date = field(default_factory=lambda: date(2026, 6, 1))
    end_date: date = field(default_factory=lambda: date(2026, 6, 2))
    target_excluded_dates: set[date] = field(default_factory=set)

    # Fixed session gap + billing policy (``from_settings`` fills them from the
    # settings port; shared with the SQL derivation and the upload path so
    # numbers cannot diverge).
    session_gap_secs: int = DEFAULT_WINDOW_GAP_SECS
    min_session_hours: float = DEFAULT_MIN_SESSION_HOURS
    round_session_hours: float = DEFAULT_ROUND_SESSION_HOURS

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

    # Odoo CSV rendering identifier (issues #497/#498/#499). Optional override
    # only: it defaults to *unset* rather than to a captured deployment constant,
    # because a plausible-but-wrong employee id in a billing artifact does not
    # surface until someone audits hours against the wrong person. The export
    # path resolves the real id at render time (``get_employee_id``, which
    # delegates to ``billing.resolve_employee_id``) and passes it to
    # :func:`~odoo_dev_core.sessionization.render_csv.render_odoo_csv`.
    #
    # The unit-of-measure and company ids that used to live here are gone: Odoo
    # populates both itself on ``account.analytic.line`` create (the company is
    # taken from the task and the UoM from that company's configured time mode),
    # so supplying them was at best a no-op and at worst a wrong attribution.
    odoo_employee_id: Optional[int] = None

    # Day-bucketing timezone (issue #378 item 11). ``from_settings`` fills it
    # from ``[behavior] day_bucket_tz``; the default is US Central. The pure
    # Transform phase reads the zone from here.
    day_bucket_tz: tzinfo = DEFAULT_DAY_BUCKET_TZ

    @classmethod
    def from_settings(
        cls, settings: SettingsView, **overrides: Any
    ) -> "SessionizationConfig":
        """Build a config whose gap, billing policy and zone come from ``settings``.

        :param settings: The resolved settings port.
        :param overrides: Any other field, passed through to the constructor.
        """
        return cls(
            session_gap_secs=settings.session_gap_secs,
            min_session_hours=settings.min_session_hours,
            round_session_hours=settings.round_session_hours,
            day_bucket_tz=settings.day_bucket_tz,
            **overrides,
        )

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
