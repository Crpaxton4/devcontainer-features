"""Composition root of the ``odoo_tui`` app: the one module that names data constructors.

:func:`bootstrap` is the ONE place the default object graph is assembled. It
builds every member of the :class:`~odoo_dev_core.ports.Services` bundle and
returns a :class:`~odoo_dev_core.registry.CommandService` over the builtin
commands. Entrypoints shrink to *parse, bootstrap, dispatch, format*.

This module is deliberately unconstrained by the layering contracts: it is the
composition root, the single module allowed to name every layer in order to
connect them. The concrete constructors (:class:`OdooClient`,
:class:`LocalConfig`, :class:`EventStore`) are re-exported here so an
entrypoint that must build a piece of the graph itself obtains the constructor
from the composition root instead of importing the data layer.
"""

import dataclasses
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from odoo_client.client import OdooClient
from odoo_client.settings import OdooConnectionSettings, build_connection_settings
from odoo_dev_core import CommandService, Services, default_registry
from odoo_dev_core.ports import RpcClient, SettingsView, StateStore
from odoo_dev_data import adapters
from odoo_dev_data.config import LocalConfig, connection_values
from odoo_dev_data.events import EventStore
from odoo_dev_data.home import resolve_home
from odoo_dev_data.jobs import JobStore
from odoo_dev_data.repo_map import (
    RepoMap,
    Resolved,
    load_repo_map,
    resolve_project,
    save_repo_map,
)
from odoo_dev_data.tasks import TaskRecord, append_friction, read_task, write_task

__all__ = [
    "assert_configured",
    "bootstrap",
    "build_client",
    "settings_from_config",
    "OdooClient",
    "LocalConfig",
    "EventStore",
]


class RepoMapStore:
    """The project-to-repo map under one state home."""

    def __init__(self, home: Path) -> None:
        self._home = home

    def load(self) -> RepoMap:
        return load_repo_map(home=self._home)

    def save(self, repo_map: RepoMap) -> Path:
        return save_repo_map(repo_map, home=self._home)

    def resolve(
        self,
        query: Optional[str] = None,
        *,
        cwd: Optional[Path] = None,
        repos_dir: Optional[Path] = None,
    ) -> Resolved:
        return resolve_project(query, cwd=cwd, repos_dir=repos_dir, home=self._home)


class TaskStore:
    """The per-task ``task.json`` records under one state home."""

    def __init__(self, home: Path) -> None:
        self._home = home

    def read(self, task_id: int | str) -> TaskRecord:
        return read_task(task_id, self._home)

    def write(self, record: TaskRecord) -> Path:
        return write_task(record, self._home)

    def append_friction(
        self,
        task_id: int | str,
        entries: Iterable[Mapping[str, Any]],
        *,
        job_id: str = "",
    ) -> int:
        return append_friction(task_id, entries, job_id=job_id, home=self._home)


class SystemClock:
    """The wall clock, as an aware UTC datetime."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


def settings_from_config(
    config: LocalConfig, **explicit: object
) -> OdooConnectionSettings:
    """Validate the connection values of ``config`` into connection settings.

    ``explicit`` keyword values win over the resolved ones.

    :raises ValueError: When a required Odoo connection setting is unresolved.
    """
    return build_connection_settings(
        dataclasses.asdict(connection_values(config, **explicit))
    )


def build_client(config: Optional[LocalConfig] = None) -> OdooClient:
    """Build the production :class:`OdooClient` from ``config``.

    ``config`` defaults to one ``LocalConfig.load()``. On an authentication
    failure the executor reloads the config file and retries once, so a rotated
    key is picked up without a restart.

    :raises ValueError: When a required Odoo connection setting is unresolved.
    """
    config = config if config is not None else LocalConfig.load()
    return OdooClient.from_settings(
        settings_from_config(config),
        credentials_refresh=lambda: settings_from_config(LocalConfig.load()),
    )


def bootstrap(
    *,
    env: Optional[Mapping[str, str]] = None,
    client: Optional[RpcClient] = None,
    events: Optional[StateStore] = None,
    config: Optional[SettingsView] = None,
) -> CommandService:
    """Assemble the default object graph and return the wired command service.

    Every omitted argument resolves to the production default, while an explicit
    argument swaps in a caller-owned instance (the CLI's lazy client wrapper, a
    test fake) without touching the wiring.

    :param env: The environment, ``os.environ`` by default. It resolves the
        state home and the config, and rides on ``Services.env``.
    :param client: Any ``RpcClient``; defaults to a fresh :class:`OdooClient`.
    :param events: The event store; defaults to an :class:`EventStore` under
        the state home.
    :param config: The resolved settings; defaults to ``LocalConfig.load(env=env)``.
    :returns: A :class:`CommandService` over every builtin command.
    """
    env = os.environ if env is None else env
    home = resolve_home(env)
    if config is None:
        config = LocalConfig.load(env=env)
    if client is None:
        client = build_client(config)
    if events is None:
        events = EventStore(home=home)
    services = Services(
        client=client,
        events=events,
        config=config,
        git=adapters.git,
        github=adapters.github,
        chatter=adapters.odoo,
        # The job ledger only matters inside a dispatched run (``ODOO_JOB_ID``).
        jobs=JobStore(home=home) if env.get("ODOO_JOB_ID") else None,
        repo_map=RepoMapStore(home),
        tasks=TaskStore(home),
        clock=SystemClock(),
        env=env,
    )
    return CommandService(default_registry(), services)


def assert_configured() -> None:
    """Raise if the entrypoint's real precondition is unmet (#642).

    What the commands need is resolvable Odoo connection settings. The failure
    already has a named, actionable error that the dispatch error boundary
    renders as a structured payload, so no new exception type is introduced.

    Only the *settings* are resolved. No :class:`~odoo_client.client.OdooClient`
    is built and no socket is opened, so the lazy-client contract holds.

    :raises ValueError: When required Odoo connection settings are unresolved.
    """
    settings_from_config(LocalConfig.load())
