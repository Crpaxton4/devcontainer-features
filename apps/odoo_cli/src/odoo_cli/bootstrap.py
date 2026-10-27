"""Composition root of the odoo-dev CLI: the one module that wires the layers.

``bootstrap()`` builds every member of the ``Services`` bundle from the data
layer (state stores, adapters, the Odoo client) and returns the
``CommandService`` over the builtin commands. The CLI subcommands that run
business logic get their service here and nowhere else. The Odoo client is
built on first use, so a local-only command needs no Odoo configuration.
"""

import dataclasses
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from odoo_client.client import OdooClient
from odoo_client.settings import OdooConnectionSettings, build_connection_settings
from odoo_dev_core import CommandService, Services, default_registry
from odoo_dev_core.ports import RpcClient, SettingsView, StateStore
from odoo_dev_data import adapters
from odoo_dev_data.config import LocalConfig, connection_values
from odoo_dev_data.db import connect
from odoo_dev_data.events import EventStore
from odoo_dev_data.home import resolve_home
from odoo_dev_data.jobs import JobStore
from odoo_dev_data.repo_map import RepoMap, Resolved, load_repo_map, resolve_project
from odoo_dev_data.repo_map import save_repo_map
from odoo_dev_data.tasks import TaskRecord, append_friction, read_task, write_task


def settings_from_config(config: Any, **explicit: object) -> OdooConnectionSettings:
    """Validate the connection values of ``config``; ``explicit`` values win.

    :raises ValueError: When a required Odoo connection setting is unresolved.
    """
    return build_connection_settings(
        dataclasses.asdict(connection_values(config, **explicit))
    )


def assert_configured(config: SettingsView) -> None:
    """Raise when the Odoo connection settings of ``config`` do not resolve.

    Only the settings are resolved: no client is built and no socket is opened.

    :raises ValueError: When required Odoo connection settings are unresolved.
    """
    settings_from_config(config)


class _LazyOdooClient:
    """An ``RpcClient`` that builds the real :class:`OdooClient` on first use."""

    def __init__(self, config: Any, env: Mapping[str, str]) -> None:
        self._config, self._env = config, env
        self._client: OdooClient | None = None

    def _resolve(self) -> OdooClient:
        if self._client is None:
            self._client = OdooClient.from_settings(
                settings_from_config(self._config),
                credentials_refresh=lambda: settings_from_config(
                    LocalConfig.load(env=self._env)
                ),
            )
        return self._client

    @property
    def uid(self) -> int:
        return self._resolve().uid

    def execute(self, model: str, method: str, *args, **kwargs):
        return self._resolve().execute(model, method, *args, **kwargs)

    def __getitem__(self, model_name: str):
        return self._resolve()[model_name]


class RepoMapStore:
    """The project-to-repo map under one state home."""

    def __init__(self, home: Path) -> None:
        self._home = home

    def load(self) -> RepoMap:
        return load_repo_map(home=self._home)

    def save(self, repo_map: RepoMap) -> Path:
        return save_repo_map(repo_map, home=self._home)

    def resolve(self, query=None, *, cwd=None, repos_dir=None) -> Resolved:
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
        self, task_id: int | str, entries: Iterable[Mapping[str, Any]], *, job_id=""
    ) -> int:
        return append_friction(task_id, entries, job_id=job_id, home=self._home)


class SystemClock:
    """The wall clock, as an aware UTC datetime."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


def bootstrap(
    *,
    env: Mapping[str, str] | None = None,
    odoo: RpcClient | None = None,
    events: StateStore | None = None,
    config: SettingsView | None = None,
) -> CommandService:
    """Assemble the default object graph and return the wired command service.

    :param env: The environment, ``os.environ`` by default.
    :param odoo: Any ``RpcClient``; defaults to a client built on first use.
    :param events: The event store; defaults to an ``EventStore`` that shares its
        ``state.db`` connection with ``Services.jobs``. An injected store has no
        connection to share, so ``Services.jobs`` is then ``None``.
    :param config: The settings; defaults to ``LocalConfig.load(env=env)``.
    """
    env = os.environ if env is None else env
    config = config or LocalConfig.load(env=env)
    home, jobs = resolve_home(env), None
    if events is None:
        conn = connect(home)
        events, jobs = EventStore(conn=conn), JobStore(conn=conn)
    services = Services(
        client=_LazyOdooClient(config, env) if odoo is None else odoo,
        events=events,
        config=config,
        git=adapters.git,
        github=adapters.github,
        chatter=adapters.odoo,
        jobs=jobs,
        repo_map=RepoMapStore(home),
        tasks=TaskStore(home),
        clock=SystemClock(),
        env=env,
    )
    return CommandService(default_registry(), services)
