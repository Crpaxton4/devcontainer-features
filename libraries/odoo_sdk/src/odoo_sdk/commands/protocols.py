"""Consumer-side ports for everything external the command layer drives (#713, #718).

ADR-005's hexagonal seam, defined where it is consumed: core (and the
surfaces that ride on core) depend on these structural Protocols, and the
concrete adapters satisfy them without registration or inheritance. One port
per external system, mirroring the ``adapters/`` package layout:

========================  =====================================  =========================
Port                      Concrete adapter                       Adapter package
========================  =====================================  =========================
:class:`RpcClient`        ``OdooClient``                         ``client/`` + ``transport/``
:class:`StateStore`       ``LocalStateClient``                   ``state/`` (+ ``adapters/state``)
:class:`GitGateway`       ``sync_git_log`` module surface        ``adapters/git``
:class:`IssueTracker`     ``sync_github`` module surface         ``adapters/github``
:class:`CalendarGateway`  Google puller module surface           ``adapters/google``
:class:`SettingsView`     ``LocalConfig``                        ``state/config``
========================  =====================================  =========================

Every member of every port is *derived from what the consumers actually
call today* — nothing speculative. The gateway ports are satisfied by the
adapter packages themselves (PEP 544 allows a module whose functions match
the protocol's methods to implement it), so the frozen module-function
pullers need no wrapping objects; core's default binding of gateway to
adapter lives in :mod:`odoo_sdk.commands.builtin.resync` and the entrypoints
inject overrides through that command's ``pullers`` seam, while
:func:`odoo_sdk.bootstrap.bootstrap` wires the concrete ``RpcClient`` /
``StateStore`` / config instances.
"""

from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Callable, Optional, Protocol

from odoo_sdk.tracking.models import EventRecord, SessionWindow, TaskRun

if TYPE_CHECKING:  # pragma: no cover
    from odoo_sdk.records.recordset import OdooRecordset
    from odoo_sdk.state import LocalConfig


class RpcClient(Protocol):
    """Structural contract for the RPC client the command layer depends on.

    :class:`~odoo_sdk.commands.command.Command` and
    :class:`~odoo_sdk.commands.command_registry.Registry` are typed against this
    Protocol instead of the concrete
    :class:`~odoo_sdk.client.client.OdooClient` so any object exposing the same
    members can drive a command. The members are exactly those the command
    bodies and their utilities use:

    * ``uid`` — the authenticated Odoo user id.
    * ``execute(model, method, ...)`` — a raw model-method call.
    * ``__getitem__(model_name)`` — a model-bound recordset.

    :class:`OdooClient` satisfies this Protocol structurally, with no change.
    ``__getitem__`` names :class:`~odoo_sdk.records.recordset.OdooRecordset`,
    which is a *core domain type* since #718 (the public API is explicitly
    recordset-first; ADR-005 amendment) — the port no longer leaks an adapter
    type.
    """

    @property
    def uid(self) -> int: ...

    def execute(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any: ...

    def __getitem__(self, model_name: str) -> "OdooRecordset": ...


class StateStore(Protocol):
    """Structural contract for the local task-tracker store core drives (#718).

    The consumer-side port for :class:`~odoo_sdk.state.LocalStateClient`
    (SQLite, host-provisioned): every member below is called today by the
    command layer, the billing/tracking workflows, or the TUI driver's
    read paths. The store's ingest-only members (``add_event_dedup``,
    ``get_event``, ``get_events``, ``update_timesheet_id``) are deliberately
    absent — only the data-side resync adapters use them, and an adapter
    talking to its own store needs no port.

    ``LocalStateClient`` satisfies this Protocol structurally, with no
    change.
    """

    # ── run FSM ─────────────────────────────────────────────────────────
    def require_active_run(self, task_id: int) -> TaskRun: ...

    def get_active_run(self, task_id: int) -> Optional[TaskRun]: ...

    def get_resumable_run(self, task_id: int) -> Optional[TaskRun]: ...

    def get_run_by_id(self, run_id: int) -> Optional[TaskRun]: ...

    def get_all_active_runs(self) -> list[TaskRun]: ...

    def get_all_runs(self) -> list[TaskRun]: ...

    def get_stopped_runs_with_timesheet(self) -> list[TaskRun]: ...

    def create_run(
        self,
        task_id: int,
        task_name: str,
        project_id: int,
        project_name: str,
        timesheet_id: Optional[int] = None,
    ) -> TaskRun: ...

    def transition_to_awaiting(self, task_id: int) -> TaskRun: ...

    def transition_to_running(self, task_id: int) -> TaskRun: ...

    def close_run(self, task_id: int) -> TaskRun: ...

    def stop_run(self, task_id: int, timesheet_id: Optional[int] = None) -> TaskRun: ...

    def abort_run(self, task_id: int) -> TaskRun: ...

    def get_aborted_runs(self) -> list[TaskRun]: ...

    def get_runs_for_task(self, task_id: int) -> list[TaskRun]: ...

    def set_run_summary(self, run_id: int, summary: str) -> None: ...

    # ── run annotations and chatter bookkeeping ─────────────────────────
    def append_note(self, task_id: int, note: str) -> None: ...

    def last_note_at(self, task_id: int) -> Optional[datetime]: ...

    def set_question_watermark(self, task_id: int, message_id: int) -> None: ...

    def get_chatter_dedupe(self, task_id: int, dedupe_key: str) -> Optional[int]: ...

    def record_chatter_dedupe(
        self, task_id: int, dedupe_key: str, message_id: int
    ) -> bool: ...

    def remap_timesheet_id(
        self, old_timesheet_id: int, new_timesheet_id: int
    ) -> None: ...

    # ── events timeseries ───────────────────────────────────────────────
    def add_event(self, event: EventRecord) -> EventRecord: ...

    def latest_event_timestamp_for_task(self, task_id: int) -> Optional[datetime]: ...

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

    def event_ids_before(self, cutoff: datetime) -> list[int]: ...

    def delete_events(self, ids: list[int]) -> int: ...

    def vacuum(self) -> None: ...

    # ── derived sessions and the upload ledger ──────────────────────────
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

    # ── settings ────────────────────────────────────────────────────────
    def get_setting(self, key: str) -> Optional[str]: ...

    def set_setting(self, key: str, value: str) -> None: ...


class GitGateway(Protocol):
    """Port for the local-git resync source (#718).

    Derived from core's one call site
    (:data:`odoo_sdk.commands.builtin.resync._SYNC_DISPATCH`): a single
    idempotent reconcile over the resolved capture window. The adapter
    package :mod:`odoo_sdk.adapters.git` satisfies it as a module
    (PEP 544); tests satisfy it with plain fakes through the ``pullers``
    seam.
    """

    def sync_git_log(
        self,
        state: StateStore,
        config: Optional["LocalConfig"] = None,
        client: Any = None,
        *,
        now: Optional[datetime] = None,
        start: Optional[date] = None,
        end: Optional[date] = None,
    ) -> dict[str, Any]: ...


class IssueTracker(Protocol):
    """Port for the GitHub activity resync source (#718).

    Derived from core's one call site
    (:data:`odoo_sdk.commands.builtin.resync._SYNC_DISPATCH`): one
    account-wide, idempotent reconcile of authored PRs, reviews, and
    comments over the resolved window. The adapter package
    :mod:`odoo_sdk.adapters.github` satisfies it as a module (PEP 544).
    (Odoo task chatter needs no sibling port: its puller reaches Odoo
    through the existing :class:`RpcClient`.)
    """

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


class CalendarGateway(Protocol):
    """Port for the opt-in Google participation sources (#718).

    Derived from core's call sites
    (:func:`odoo_sdk.commands.builtin.resync._run_google`): the two Google
    pullers are invoked identically — ``puller(state, config)`` — and both
    raise (``GoogleAuthError`` / ``GoogleAPIError`` / ``ValueError``) rather
    than skip, so core guards them behind one error boundary. One port
    covers the one external system (Google): Calendar meeting-tick series
    and sent-Gmail point events. The adapter package
    :mod:`odoo_sdk.adapters.google` satisfies it as a module (PEP 544).
    """

    def sync_google_calendar(
        self,
        state: StateStore,
        config: "LocalConfig",
        *,
        transport: Callable[..., dict] = ...,
        now: Optional[datetime] = None,
    ) -> dict[str, Any]: ...

    def sync_gmail(
        self,
        state: StateStore,
        config: "LocalConfig",
        *,
        transport: Callable[..., dict] = ...,
        now: Optional[datetime] = None,
    ) -> dict[str, Any]: ...


class SettingsView(Protocol):
    """Read-only resolved-settings surface a surface driver consumes (#718).

    The TUI driver needs exactly one knob from the injected
    :class:`~odoo_sdk.state.LocalConfig` today (the session gap it prints in
    the empty-window hint); this consumer-side view names that surface so
    the driver's dataclass annotations stop importing the data layer. Widen
    it only when a surface legitimately reads more. ``LocalConfig``
    satisfies it structurally, with no change.
    """

    @property
    def session_gap_mins(self) -> int: ...
