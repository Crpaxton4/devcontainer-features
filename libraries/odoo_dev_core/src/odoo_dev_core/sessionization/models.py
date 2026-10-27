"""Pure data structures for the sessionization ETL.

This module is state-agnostic: it defines only the normalized event model,
the computed time-entry render model, and the result containers produced by the
Transform phase. It performs no I/O and is unaware of SQLite, GitHub, git, the
filesystem, or MCP.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto

# Default fixed sessionization gap (seconds). The billing floor/step share the
# upload path's hour-denominated knobs (``min_session_hours`` /
# ``round_session_hours``), so no minute-denominated billing constant exists.
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
    # PR opened by the tracked user — billable, review family (resync, #656)
    PR_OPENED = auto()


@dataclass
class RawEvent:
    """A normalized event lifted from any source (git, GitHub, agent)."""

    timestamp: datetime  # tz-aware UTC
    task_ids: list[str]  # extracted task IDs (may be empty -> ["UNKNOWN"])
    repo: str  # "owner/repo"
    pr_num: int  # 0 for local-git commits or agent events with no PR
    event_type: EventType
    branch: str = ""
    is_release: bool = (
        False  # multi-task on a release-bearing source (not agent/claude hooks)
    )
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
