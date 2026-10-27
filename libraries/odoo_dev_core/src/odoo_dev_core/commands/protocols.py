"""Consumer-side ports for everything external the command layer drives (#713, #718).

ADR-005's hexagonal seam, defined where it is consumed: core (and the
surfaces that ride on core) depend on these structural Protocols, and the
concrete adapters satisfy them without registration or inheritance. One port
per external system:

========================  ===========================  ================================
Port                      Concrete adapter             Adapter module
========================  ===========================  ================================
:class:`RpcClient`        ``OdooClient``               ``client/`` + ``transport/``
:class:`StateStore`       ``EventStore``               ``odoo_dev_data.events``
:class:`GitGateway`       ``sync_git_log``             ``odoo_dev_data.adapters.git``
:class:`IssueTracker`     ``sync_github``              ``odoo_dev_data.adapters.github``
:class:`SettingsView`     ``LocalConfig``              ``odoo_dev_data.config``
========================  ===========================  ================================

Every member of every port is *derived from what the consumers actually
call today* — nothing speculative. The gateway ports are satisfied by the
adapter packages themselves (PEP 544 allows a module whose functions match
the protocol's methods to implement it), so the frozen module-function
pullers need no wrapping objects; core's default binding of gateway to
adapter lives in :mod:`odoo_dev_core.commands.builtin.resync` and the entrypoints
inject overrides through that command's ``pullers`` seam, while
the ``bootstrap()`` of each app wires the concrete ``RpcClient`` /
``StateStore`` / config instances.
"""

from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, Protocol

from odoo_dev_data.events import EventRecord, SessionWindow

if TYPE_CHECKING:  # pragma: no cover
    from odoo_client.records.recordset import OdooRecordset
    from odoo_dev_data.config import LocalConfig


class RpcClient(Protocol):
    """Structural contract for the RPC client the command layer depends on.

    :class:`~odoo_dev_core.commands.command.Command` and
    :class:`~odoo_dev_core.commands.command_registry.Registry` are typed against this
    Protocol instead of the concrete
    :class:`~odoo_client.client.client.OdooClient` so any object exposing the same
    members can drive a command. The members are exactly those the command
    bodies and their utilities use:

    * ``uid`` — the authenticated Odoo user id.
    * ``execute(model, method, ...)`` — a raw model-method call.
    * ``__getitem__(model_name)`` — a model-bound recordset.

    :class:`OdooClient` satisfies this Protocol structurally, with no change.
    ``__getitem__`` names :class:`~odoo_client.records.recordset.OdooRecordset`,
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

    The consumer-side port for :class:`~odoo_dev_data.events.EventStore`
    (SQLite, host-provisioned): every member below is called today by the
    command layer, the billing/tracking workflows, or the TUI driver's
    read paths. The store's ingest-only members (``add_event_dedup``,
    ``get_event``, ``get_events``) are deliberately
    absent — only the data-side resync adapters use them, and an adapter
    talking to its own store needs no port.

    ``EventStore`` satisfies this Protocol structurally, with no
    change.
    """

    # ── chatter bookkeeping ─────────────────────────────────────────────
    def get_chatter_dedupe(self, task_id: int, dedupe_key: str) -> Optional[int]: ...

    def record_chatter_dedupe(
        self, task_id: int, dedupe_key: str, message_id: int
    ) -> bool: ...

    # ── events timeseries ───────────────────────────────────────────────
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
    (:data:`odoo_dev_core.commands.builtin.resync._SYNC_DISPATCH`): a single
    idempotent reconcile over the resolved capture window. The adapter
    module :mod:`odoo_dev_data.adapters.git` satisfies it as a module
    (PEP 544); tests satisfy it with plain fakes through the ``pullers``
    seam.
    """

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


class IssueTracker(Protocol):
    """Port for the GitHub activity resync source (#718).

    Derived from core's one call site
    (:data:`odoo_dev_core.commands.builtin.resync._SYNC_DISPATCH`): one
    account-wide, idempotent reconcile of authored PRs, reviews, and
    comments over the resolved window. The adapter package
    :mod:`odoo_dev_data.adapters.github` satisfies it as a module (PEP 544).
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


class SettingsView(Protocol):
    """Read-only resolved-settings surface a surface driver consumes (#718).

    The TUI driver needs exactly one knob from the injected
    :class:`~odoo_dev_data.config.LocalConfig` today (the session gap it prints in
    the empty-window hint); this consumer-side view names that surface so
    the driver's dataclass annotations stop importing the data layer. Widen
    it only when a surface legitimately reads more. ``LocalConfig``
    satisfies it structurally, with no change.
    """

    @property
    def session_gap_mins(self) -> int: ...
