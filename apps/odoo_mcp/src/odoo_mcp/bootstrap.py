"""Composition root of the ``odoo_mcp`` app.

:func:`bootstrap` builds the ``Services`` bundle and returns the command service
the tools run through. It is the one module that names the data constructors.
The event store and the job ledger share one ``state.db`` connection.
The Odoo client is built eagerly. Pass ``odoo`` or ``events`` for a test double.
"""

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
from odoo_dev_data import adapters
from odoo_dev_data.config import LocalConfig, connection_values
from odoo_dev_data.db import connect
from odoo_dev_data.events import EventStore
from odoo_dev_data.home import resolve_home
from odoo_dev_data.jobs import JobStore
from odoo_dev_data.repo_map import load_repo_map, resolve_project, save_repo_map
from odoo_dev_data.tasks import append_friction, read_task, write_task


def settings_from_config(config: LocalConfig) -> OdooConnectionSettings:
    """Validate the connection values of ``config`` into connection settings."""
    return build_connection_settings(dataclasses.asdict(connection_values(config)))


def assert_configured(config: LocalConfig) -> None:
    """Raise ``ValueError`` when ``config`` lacks a required Odoo connection setting."""
    settings_from_config(config)


def bootstrap(
    *,
    env: Optional[Mapping[str, str]] = None,
    odoo: Optional[RpcClient] = None,
    events: Optional[StateStore] = None,
    config: Optional[SettingsView] = None,
) -> CommandService:
    """Assemble the default object graph and return the wired command service."""
    env = os.environ if env is None else env
    home = resolve_home(env)
    config = LocalConfig.load(env=env) if config is None else config
    if odoo is None:
        odoo = OdooClient.from_settings(
            settings_from_config(config),
            credentials_refresh=lambda: settings_from_config(LocalConfig.load(env=env)),
        )
    conn = connect(home) if events is None else None
    jobs = JobStore(conn=conn) if conn else None
    events = EventStore(conn=conn) if conn else events
    services = Services(
        client=odoo,
        events=events,
        config=config,
        git=adapters.git,
        github=adapters.github,
        chatter=adapters.odoo,
        jobs=jobs,
        repo_map=SimpleNamespace(
            load=partial(load_repo_map, home=home),
            save=partial(save_repo_map, home=home),
            resolve=partial(resolve_project, home=home),
        ),
        tasks=SimpleNamespace(
            read=partial(read_task, home=home),
            write=partial(write_task, home=home),
            append_friction=partial(append_friction, home=home),
        ),
        clock=SimpleNamespace(now=lambda: datetime.now(timezone.utc)),
        env=env,
    )
    return CommandService(default_registry(), services)
