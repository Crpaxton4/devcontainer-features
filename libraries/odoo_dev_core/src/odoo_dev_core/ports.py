"""Consumer-side ports for everything external the command layer drives.

Core depends on these structural Protocols. The concrete adapters satisfy them
without registration or inheritance. The app bootstrap builds one adapter per
port and hands them to core in a :class:`Services` bundle.

========================  ===========================  ================================
Port                      Concrete adapter             Adapter module
========================  ===========================  ================================
:class:`RpcClient`        ``OdooClient``               ``odoo_client``
:class:`StateStore`       ``EventStore``               ``odoo_dev_data.events``
:class:`GitGateway`       the ``git`` module           ``odoo_dev_data.adapters.git``
:class:`IssueTracker`     the ``github`` module        ``odoo_dev_data.adapters.github``
:class:`ChatterGateway`   the ``odoo`` module          ``odoo_dev_data.adapters.odoo``
:class:`SettingsView`     ``LocalConfig``              ``odoo_dev_data.config``
:class:`JobsPort`         ``JobStore``                 ``odoo_dev_data.jobs``
:class:`RepoMapPort`      ``RepoMapStore``             the app bootstrap
:class:`TaskRecords`      ``TaskStore``                the app bootstrap
:class:`Clock`            ``SystemClock``              the app bootstrap
========================  ===========================  ================================

Every member is derived from what the handlers call. The gateway ports are
satisfied by the adapter modules themselves (PEP 544 allows a module whose
functions match the protocol's methods to implement it).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Mapping,
    Optional,
    Protocol,
    runtime_checkable,
)

from odoo_dev_data.events import EventRecord, SessionWindow

if TYPE_CHECKING:  # pragma: no cover
    from zoneinfo import ZoneInfo

    from odoo_client.records.recordset import OdooRecordset
    from odoo_dev_data.config import LocalConfig
    from odoo_dev_data.repo_map import RepoMap, Resolved
    from odoo_dev_data.tasks import TaskRecord


@runtime_checkable
class RpcClient(Protocol):
    """Structural contract for the RPC client the handlers depend on.

    * ``uid`` - the authenticated Odoo user id.
    * ``execute(model, method, ...)`` - a raw model-method call.
    * ``__getitem__(model_name)`` - a model-bound recordset.
    """

    @property
    def uid(self) -> int: ...

    def execute(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any: ...

    def __getitem__(self, model_name: str) -> "OdooRecordset": ...


@runtime_checkable
class StateStore(Protocol):
    """Structural contract for the local task-tracker store core drives.

    ``EventStore`` satisfies it. The store's ingest-only members
    (``add_event_dedup``, ``get_event``, ``get_events``) are absent: only the
    data-side resync adapters use them.
    """

    # chatter bookkeeping
    def get_chatter_dedupe(self, task_id: int, dedupe_key: str) -> Optional[int]: ...

    def record_chatter_dedupe(
        self, task_id: int, dedupe_key: str, message_id: int
    ) -> bool: ...

    # events timeseries
    def add_event(self, event: EventRecord) -> EventRecord: ...

    def get_task_events(
        self,
        task_id: str,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> list[EventRecord]: ...

    def get_unattributed_events(
        self, start: Optional[datetime] = None, end: Optional[datetime] = None
    ) -> list[EventRecord]: ...

    def get_events_by_ids(self, ids: list[int]) -> list[EventRecord]: ...

    def count_events(
        self, start: Optional[datetime] = None, end: Optional[datetime] = None
    ) -> int: ...

    def assign_event_task_ids(self, event_ids: list[int], task_id: int) -> int: ...

    # derived sessions and the upload ledger
    def derive_sessions_overlapping(
        self,
        start: datetime,
        end: datetime,
        *,
        gap_secs: int,
        task_id: Optional[str] = None,
        repo: Optional[str] = None,
    ) -> list[SessionWindow]: ...

    def get_session_upload(self, session_key: str) -> Optional[dict]: ...

    def list_session_uploads(self) -> list[dict]: ...

    def delete_session_upload(self, session_key: str) -> None: ...

    def record_session_upload(
        self,
        session_key: str,
        timesheet_id: int,
        hours: float,
        *,
        task_id: Optional[str] = None,
        started_at: Optional[datetime] = None,
        ended_at: Optional[datetime] = None,
    ) -> None: ...

    # settings
    def get_setting(self, key: str) -> Optional[str]: ...

    def set_setting(self, key: str, value: str) -> None: ...


@runtime_checkable
class GitGateway(Protocol):
    """Port for the local-git resync source."""

    def sync_git_log(
        self,
        state: StateStore,
        config: Optional["LocalConfig"] = None,
        client: Any = None,
        *,
        root: Optional[Path] = None,
        now: Optional[datetime] = None,
        start: Optional[date] = None,
        end: Optional[date] = None,
    ) -> dict[str, Any]: ...


@runtime_checkable
class IssueTracker(Protocol):
    """Port for the GitHub activity resync source."""

    def sync_github(
        self,
        state: StateStore,
        config: Optional["LocalConfig"] = None,
        client: Any = None,
        *,
        now: Optional[datetime] = None,
        start: Optional[date] = None,
        end: Optional[date] = None,
    ) -> dict[str, Any]: ...


@runtime_checkable
class ChatterGateway(Protocol):
    """Port for the Odoo task-chatter resync source."""

    def sync_odoo_chatter(
        self,
        client: Any,
        state: StateStore,
        config: Optional["LocalConfig"] = None,
        *,
        now: Optional[datetime] = None,
        start: Optional[date] = None,
        end: Optional[date] = None,
    ) -> dict[str, Any]: ...


@runtime_checkable
class SettingsView(Protocol):
    """Read and model-id write surface of the resolved SDK settings.

    ``LocalConfig`` satisfies it. Every member is read by a handler, a
    billing helper or an app driver.
    """

    @property
    def session_gap_secs(self) -> int: ...

    @property
    def session_gap_mins(self) -> int: ...

    @property
    def min_session_hours(self) -> float: ...

    @property
    def round_session_hours(self) -> float: ...

    @property
    def day_bucket_tz(self) -> "ZoneInfo": ...

    @property
    def resync_window_days(self) -> int: ...

    @property
    def resync_authors(self) -> list[str]: ...

    @property
    def model_ids(self) -> dict[str, int]: ...

    @property
    def profiling(self) -> bool: ...

    def model_id(self, model: str) -> int | None: ...

    def require_model_id(self, model: str) -> int: ...

    def set_model_id(self, model: str, ir_model_id: int) -> Path: ...

    def get(self, key: str, default: Any = None) -> Any: ...


@runtime_checkable
class JobsPort(Protocol):
    """Port for the dispatcher job ledger a dispatched run reports into."""

    def increment_questions(self, job_id: str) -> Any: ...

    def list_finished(
        self, *, start_us: int | None = None, end_us: int | None = None
    ) -> list: ...


@runtime_checkable
class RepoMapPort(Protocol):
    """Port for the project-to-repo map."""

    def load(self) -> "RepoMap": ...

    def save(self, repo_map: "RepoMap") -> Path: ...

    def resolve(
        self,
        query: str | None = None,
        *,
        cwd: Path | None = None,
        repos_dir: Path | None = None,
    ) -> "Resolved": ...


@runtime_checkable
class TaskRecords(Protocol):
    """Port for the per-task ``task.json`` records."""

    def read(self, task_id: int | str) -> "TaskRecord": ...

    def write(self, record: "TaskRecord") -> Path: ...

    def append_friction(
        self, task_id: int | str, entries: Any, *, job_id: str = ""
    ) -> int: ...


@runtime_checkable
class Clock(Protocol):
    """Port for the current time. ``now`` returns an aware UTC datetime."""

    def now(self) -> datetime: ...


@dataclass(frozen=True)
class Services:
    """Every port a handler can use. The app bootstrap builds each member."""

    client: RpcClient
    events: StateStore
    config: SettingsView
    git: GitGateway
    github: IssueTracker
    chatter: ChatterGateway
    jobs: JobsPort | None
    repo_map: RepoMapPort
    tasks: TaskRecords
    clock: Clock
    env: Mapping[str, str]
