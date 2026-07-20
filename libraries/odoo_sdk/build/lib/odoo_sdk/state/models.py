"""Data models and error taxonomy for the local task-session state layer."""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class TaskState(str, Enum):
    RUNNING = "RUNNING"
    AWAITING_ANSWERS = "AWAITING_ANSWERS"
    STOPPED = "STOPPED"


class TrackerStateMissingError(RuntimeError):
    """Raised when the central tracker database does not exist at its path.

    The tracker database is host-provisioned state (issue #369): it is created
    on the host by ``setup.sh`` / ``setup.ps1`` and bind-mounted into every
    container. The SDK deliberately never creates it — a self-created DB would be
    container-local and discarded on rebuild, silently splitting one person's
    timeline. So every state-touching entry point raises this single, actionable
    error naming the expected path rather than materializing an empty DB.
    """


class TaskAlreadyRunningError(RuntimeError):
    """Raised when start_task is called for a task that already has an active session."""


class TaskNotRunningError(RuntimeError):
    """Raised when an operation requires an active session but none exists."""


class InvalidStateTransitionError(RuntimeError):
    """Raised when a state transition is not permitted by the FSM."""


@dataclass
class TaskRun:
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
    aborted_at: Optional[datetime] = None
    """When the run was force-aborted (never billed), else ``None`` (#356).

    An additive, nullable stamp set by :meth:`LocalStateClient.abort_run`. The
    upload path excludes any derived session lying wholly within the aborted
    run's ``[started_at, aborted_at]`` window for the same task, so an aborted
    run's leftover events never bill. A normally stopped run leaves this ``None``.
    """

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
    external_id: Optional[str] = None
    """Stable external identity for idempotent resync ingestion.

    Set by the external-sync pullers (``git:<sha>``, ``gh:pr:<n>``,
    ``gh:review:<id>``, ``odoo:mail:<id>``) so a re-run dedupes against the
    partial unique index on ``events(external_id)``. ``None`` for events with no
    external origin (hook / agent / FSM-driven writes), which never dedupe.
    """


@dataclass
class SessionWindow:
    """A per-task computed time window derived from the ``events`` timeseries.

    Windows are not stored: they are computed at query time by the SQL-derived
    read path (:meth:`~odoo_sdk.state.LocalStateClient.derive_sessions_overlapping`)
    and returned to callers. They live alongside the ``task_runs`` FSM store
    rather than replacing it.
    """

    id: Optional[int]
    task_id: str
    repo: str
    started_at: datetime
    ended_at: datetime
    strategy_name: str = "development"
    category: str = "Development"
    pr_num: int = 0
    event_ids: tuple[int, ...] = ()
    """Ids of the events that compose this window, in ascending order.

    Populated by the SQL-derived read path (``derive_sessions_overlapping``); the
    window's ``id`` is the *minimum* of these, so it is stable under append-only
    tail writes (a closed session's earliest event never changes).
    """

    @property
    def duration_seconds(self) -> float:
        """Return the window duration in seconds."""
        return (self.ended_at - self.started_at).total_seconds()


def session_key(window: SessionWindow) -> str:
    """Return a stable identity string for a derived session window.

    The key is ``"{task_id}|{id}"`` where ``id`` is the window's minimum event
    id. As of #352 sessions partition by task only (the repo is display-only
    metadata, no longer part of the identity), so a task's agent events
    (``repo=""``) and its resync'd commits (``repo="owner/repo"``) share one key
    rather than splitting into two parallel lanes. Because a closed session's
    earliest event id is immutable under append-only tail writes, the key stays
    stable across re-derivations and is the idempotency key for per-session
    timesheet uploads.
    """
    return f"{window.task_id}|{window.id}"
