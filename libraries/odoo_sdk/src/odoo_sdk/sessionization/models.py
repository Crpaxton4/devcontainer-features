"""Pure data structures for the sessionization ETL.

This module is state-agnostic: it defines only the normalized event model,
the computed time-entry render model, and the result containers produced by the
Transform phase. It performs no I/O and is unaware of SQLite, GitHub, git, the
filesystem, or MCP.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone, tzinfo
from enum import Enum, auto


def resolve_day_bucket_tz() -> tzinfo:
    """Return the configured day-bucketing timezone (issue #378 item 11).

    Reads the standard config resolver (``[behavior] day_bucket_tz`` /
    ``ODOO_DAY_BUCKET_TZ``, default ``America/Chicago``). The import is local so
    this state-agnostic module carries no import-time dependency on the state
    layer and so each call re-resolves the current config (a test can move the
    zone via an environment variable and see the new bucketing immediately).
    """
    from odoo_sdk.state.config import LocalConfig

    return LocalConfig.load().day_bucket_tz


def resolve_session_gap_secs() -> int:
    """Return the fixed sessionization inactivity gap in seconds (issue #404).

    Reads the single first-class knob (``[behavior] session_gap_secs`` /
    ``ODOO_SESSION_GAP_MINS``) so the pure Transform phase re-windows on exactly
    the gap the SQL-derived billing path (``derive_sessions_overlapping``) uses.
    The import is local so this state-agnostic module carries no import-time
    dependency on the state layer and each call re-resolves the current config.
    """
    from odoo_sdk.state.config import LocalConfig

    return LocalConfig.load().session_gap_secs


def resolve_min_session_hours() -> float:
    """Return the per-session billing floor in hours (issue #404).

    Reads the same ``[behavior] min_session_hours`` / ``ODOO_MIN_SESSION_HOURS``
    knob the upload path bills with, so the diagnostic gap-sweep and export
    round identically to what an upload writes. Resolved lazily per call.
    """
    from odoo_sdk.state.config import LocalConfig

    return LocalConfig.load().min_session_hours


def resolve_round_session_hours() -> float:
    """Return the per-session rounding step in hours (issue #404).

    Reads the same ``[behavior] round_session_hours`` / ``ODOO_ROUND_SESSION_HOURS``
    knob the upload path bills with (half-up rounding), so the diagnostic
    gap-sweep and export never report hours an upload would not write.
    """
    from odoo_sdk.state.config import LocalConfig

    return LocalConfig.load().round_session_hours


# Day-bucketing timezone reference used by scoring and rendering. Was a hardcoded
# EDT (UTC-4) offset, which mis-bucketed the US-Central user's midnight-crossing
# evening sessions; now config-driven (default US Central). This module-level
# value is the resolved default for display helpers that carry no config;
# ``SessionizationConfig.day_bucket_tz`` carries a per-run copy so the pure
# Transform phase reads the zone from its config object, not this global.
ET = resolve_day_bucket_tz()

# Default fixed sessionization gap (seconds). The billing floor/step now share the
# upload path's hour-denominated knobs (``min_session_hours`` /
# ``round_session_hours``), so no minute-denominated billing constant survives.
DEFAULT_WINDOW_GAP_SECS = 3600


class EventType(Enum):
    """Normalized source event kinds understood by the ETL."""

    COMMIT = auto()
    MERGE = auto()
    REVIEW = auto()
    AGENT = auto()
    CHATTER = auto()  # Odoo task chatter authored by the tracked user (resync)
    CALENDAR = auto()  # Google Calendar meeting tick (synthetic; resync, #370)
    EMAIL = auto()  # Sent Gmail message, a point event (resync, #370)
    CLAUDE_HOOK = auto()  # Claude Code hook activity (source ``claude:<HookName>``)


@dataclass
class RawEvent:
    """A normalized event lifted from any source (git, GitHub, agent)."""

    timestamp: datetime  # tz-aware UTC
    task_ids: list[str]  # extracted task IDs (may be empty -> ["UNKNOWN"])
    repo: str  # "owner/repo"
    pr_num: int  # 0 for local-git commits or agent events with no PR
    event_type: EventType
    branch: str = ""
    is_release: bool = False  # multi-task on a release-bearing source (not agent/claude hooks)
    subject: str = ""
    pr_title: str = ""
    pr_body: str = ""


@dataclass
class TimeEntry:
    """A computed, bounded time block attributed to one task.

    A render/view model only (issue #404): the SQL derivation
    (``derive_sessions_overlapping``) is the single sessionization algorithm, and
    both the diagnostic gap-sweep and the TUI export project their windows onto
    this shape purely so the #105 markdown/CSV renderers stay reusable. ``end``
    already carries the billed duration (start + billed span), so a renderer bills
    a row by reading ``end - start`` without re-applying any policy.
    """

    task_id: str
    repo: str
    pr_num: int
    start: datetime  # tz-aware UTC
    end: datetime  # tz-aware UTC (start + billed span)
    label: str = ""  # "owner/repo"
    branch: str = ""
    source_events: list[RawEvent] = field(default_factory=list)
    strategy_name: str = "development"
    strategy_category: str = "Development"
    activity_type: str = ""

    @property
    def duration_secs(self) -> int:
        """Return the entry duration in whole seconds."""
        return int((self.end - self.start).total_seconds())


@dataclass
class SweepResults:
    """Aggregated output of the gap sweep across all tested gap values."""

    gap_vals: list[int]  # gap values tested (minutes)
    combined: list[float]  # total secs at each gap
    per_task: dict[str, list[float]]  # task_id -> secs at each gap
    scores: list[float]  # average per-target-day score at each gap
    obs_mean: float
    best_gap: int  # minutes
    best_total: float  # secs
    best_score: float
    num_days: int


@dataclass
class TransformResult:
    """Complete, fully-computed output of the Transform phase.

    Renderers accept only this object; they call no Transform functions.
    """

    all_entries: list[TimeEntry]  # entries at default gap
    best_gap_entries: list[TimeEntry]  # entries at best (swept) gap
    sweep: SweepResults
    raw_events: list[RawEvent] = field(default_factory=list)


def utc_now() -> datetime:
    """Return the current tz-aware UTC time (kept trivial for testability)."""
    return datetime.now(timezone.utc)
