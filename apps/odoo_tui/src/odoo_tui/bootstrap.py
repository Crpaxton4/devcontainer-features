"""Composition root of ``odoo_tui``: the one module that names data constructors."""

import dataclasses
import os
from datetime import datetime, timezone
from functools import partial
from types import SimpleNamespace
from typing import Mapping, Optional

from odoo_client.client import OdooClient
from odoo_client.settings import OdooConnectionSettings, build_connection_settings
from odoo_dev_core import CommandService, Services, default_registry
from odoo_dev_core.ports import RpcClient, SettingsView, StateStore
from odoo_dev_data import adapters, repo_map, tasks
from odoo_dev_data.config import LocalConfig, connection_values
from odoo_dev_data.db import connect
from odoo_dev_data.events import EventStore
from odoo_dev_data.home import resolve_home
from odoo_dev_data.jobs import JobStore

__all__ = ["assert_configured", "bootstrap", "build_client"]


def _settings(config: LocalConfig) -> OdooConnectionSettings:
    return build_connection_settings(dataclasses.asdict(connection_values(config)))


def build_client(config: LocalConfig) -> OdooClient:
    """Build the production client; on an auth failure it reloads the config."""
    return OdooClient.from_settings(
        _settings(config), credentials_refresh=lambda: _settings(LocalConfig.load())
    )


def bootstrap(
    *,
    env: Optional[Mapping[str, str]] = None,
    odoo: Optional[RpcClient] = None,
    events: Optional[StateStore] = None,
    config: Optional[SettingsView] = None,
) -> CommandService:
    """Assemble the command service; an explicit argument replaces its default."""
    env = os.environ if env is None else env
    home = resolve_home(env)
    config = LocalConfig.load(env=env) if config is None else config
    jobs = None
    if events is None:  # the default store shares one connection with the ledger
        conn = connect(home)
        events, jobs = EventStore(conn=conn), JobStore(conn=conn)
    services = Services(
        client=odoo if odoo is not None else build_client(config),
        events=events,
        config=config,
        git=adapters.git,
        github=adapters.github,
        chatter=adapters.odoo,
        jobs=jobs,
        repo_map=SimpleNamespace(
            load=partial(repo_map.load_repo_map, home=home),
            save=partial(repo_map.save_repo_map, home=home),
            resolve=partial(repo_map.resolve_project, home=home),
        ),
        tasks=SimpleNamespace(
            read=partial(tasks.read_task, home=home),
            write=partial(tasks.write_task, home=home),
            append_friction=partial(tasks.append_friction, home=home),
        ),
        clock=SimpleNamespace(now=lambda: datetime.now(timezone.utc)),
        env=env,
    )
    return CommandService(default_registry(), services)


def assert_configured() -> None:
    """Raise ``ValueError`` when the Odoo connection settings are unresolved."""
    _settings(LocalConfig.load())
