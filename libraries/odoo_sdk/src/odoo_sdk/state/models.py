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
