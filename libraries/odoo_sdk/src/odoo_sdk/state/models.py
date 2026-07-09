"""Data models and error taxonomy for the local task-session state layer."""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class TaskState(str, Enum):
    RUNNING = "RUNNING"
    AWAITING_ANSWERS = "AWAITING_ANSWERS"
    STOPPED = "STOPPED"


class ProjectIdError(RuntimeError):
    """Raised when the git remote origin URL cannot be determined."""


class TaskAlreadyRunningError(RuntimeError):
    """Raised when start_task is called for a task that already has an active session."""


class TaskNotRunningError(RuntimeError):
    """Raised when an operation requires an active session but none exists."""


class InvalidStateTransitionError(RuntimeError):
    """Raised when a state transition is not permitted by the FSM."""


@dataclass
class TaskSession:
    id: int
    task_id: int
    task_name: str
    project_id: int
    project_name: str
    state: TaskState
    started_at: datetime
    stopped_at: Optional[datetime]
    timesheet_id: Optional[int]
    notes: list[str]

    @property
    def elapsed_seconds(self) -> float:
        end = self.stopped_at or datetime.now(timezone.utc)
        return (end - self.started_at).total_seconds()

    @property
    def elapsed_hours(self) -> float:
        return self.elapsed_seconds / 3600

    @property
    def elapsed_human(self) -> str:
        total = int(self.elapsed_seconds)
        h, rem = divmod(total, 3600)
        m, s = divmod(rem, 60)
        return f"{h}h {m}m {s}s"


@dataclass
class EventRecord:
    """A point-in-time event row in the unified ``events`` timeseries table.

    This is the persistence-layer twin of the pure
    :class:`odoo_sdk.sessionization.RawEvent`. It is typed by ``source`` (e.g.
    ``commit``, ``merge``, ``review``, ``agent``) and carries the extracted task
    identifiers as a JSON list so a single event may attribute to several tasks.
    """

    id: Optional[int]
    source: str
    timestamp: datetime
    task_ids: list[str]
    repo: str
    pr_num: int = 0
    branch: str = ""
    subject: str = ""
    payload: Optional[dict] = None
    session_id: Optional[int] = None
    """The ``sessions`` row this event is linked to, or ``None`` when unlinked.

    Every non-excluded event is linked to exactly one session by the incremental
    sessionizer; this column is the persisted event to session edge that lets
    ingestion be maintained incrementally rather than rebuilt from scratch.
    """


@dataclass
class SessionWindow:
    """A per-task computed time window in the unified ``sessions`` table.

    Rows are produced by the sessionization ETL and are queryable by date range.
    They live alongside the ``task_sessions`` FSM store rather than replacing it.
    """

    id: Optional[int]
    task_id: str
    repo: str
    started_at: datetime
    ended_at: datetime
    strategy_name: str = "development"
    category: str = "Development"
    pr_num: int = 0

    @property
    def duration_seconds(self) -> float:
        """Return the window duration in seconds."""
        return (self.ended_at - self.started_at).total_seconds()
