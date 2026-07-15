"""Pure data structures for the sessionization ETL.

This module is state-agnostic: it defines only the normalized event model,
the computed time-entry model, strategy configuration rows, and the result
containers produced by the Transform phase. It performs no I/O and is unaware
of SQLite, GitHub, git, the filesystem, or MCP.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum, auto

# Eastern Time reference used for day bucketing in scoring and rendering.
# EDT (UTC-4); adjust the offset for winter (UTC-5) when required by callers.
ET = timezone(timedelta(hours=-4), "ET")

# Default window / billing constants (seconds unless noted).
DEFAULT_WINDOW_GAP_SECS = 3600
DEFAULT_MIN_TASK_MINUTES = 15
DEFAULT_BILLING_STEP_MINS = 15


class EventType(Enum):
    """Normalized source event kinds understood by the ETL."""

    COMMIT = auto()
    MERGE = auto()
    REVIEW = auto()
    AGENT = auto()
    CHATTER = auto()  # Odoo task chatter authored by the tracked user (resync)
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
    """A computed, bounded time block attributed to one task."""

    task_id: str
    repo: str
    pr_num: int
    start: datetime  # tz-aware UTC
    end: datetime  # tz-aware UTC
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


@dataclass(frozen=True)
class SessionStrategyConfig:
    """Flat configuration row for one sessionization strategy."""

    name: str
    category: str
    event_types: tuple[EventType, ...]
    strategy_kind: str
    group_keys: tuple[str, ...]
    gap_secs: int = DEFAULT_WINDOW_GAP_SECS
    fixed_secs: int = DEFAULT_MIN_TASK_MINUTES * 60
    billing_floor_secs: int = DEFAULT_MIN_TASK_MINUTES * 60
    billing_step_secs: int = DEFAULT_BILLING_STEP_MINS * 60
    sweep_enabled: bool = False
    context_fields: tuple[str, ...] = ("pr_title", "subject", "pr_body")
    context_limit: int = 220
    fallback_action: str = "advanced project work"
    priority: int = 100


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
