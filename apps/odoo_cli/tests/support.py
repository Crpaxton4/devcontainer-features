"""Shared test helpers: fake-injected services and a console-script style ``main``.

:class:`~odoo_dev_data.events.EventStore` creates ``state.db`` under
``$ODOO_DEV_HOME`` on first connect, so a test needs only a fresh home directory.
"""

import dataclasses
import tempfile
from pathlib import Path
from typing import Optional
from unittest.mock import patch

from odoo_dev_core import CommandService
from odoo_dev_data.config import LocalConfig
from odoo_dev_data.events import EventStore

from odoo_cli.__main__ import main
from odoo_cli.bootstrap import bootstrap

#: A config whose Odoo connection settings resolve (the ``assert_configured`` guard).
CONFIGURED = LocalConfig(
    connection={
        "url": "https://example.odoo.com",
        "db": "example",
        "username": "bot",
        "password": "secret",
    }
)


def make_event_store(home: Optional[Path] = None) -> EventStore:
    """Return an :class:`EventStore` on ``home``, or on a fresh temp home.

    With no ``home`` a new ``tempfile.mkdtemp()`` directory is used, so each
    call yields an empty, isolated store.
    """
    if home is None:
        home = Path(tempfile.mkdtemp())
    return EventStore(home=home)


def make_service(
    events: Optional[EventStore] = None,
    client: object = None,
    config: Optional[LocalConfig] = None,
    env: Optional[dict] = None,
    **services: object,
) -> CommandService:
    """Return the real command service over the given fakes (the CLI's seam).

    The fakes go in through ``bootstrap(odoo=, events=, config=)``. Extra keyword
    arguments replace members of the ``Services`` bundle.
    """
    built = bootstrap(
        env={} if env is None else env,
        odoo=client if client is not None else object(),
        events=events if events is not None else make_event_store(),
        config=config if config is not None else CONFIGURED,
    )
    if not services:
        return built
    return CommandService(
        built.registry, dataclasses.replace(built.services, **services)
    )


def serving(service: CommandService):
    """Make the CLI's ``bootstrap()`` call return ``service`` inside the block."""
    return patch("odoo_cli.commands.bootstrap", return_value=service)


def invoke(argv=None) -> None:
    """Call ``main`` like the console script: a non-zero code raises ``SystemExit``."""
    code = main(argv)
    if code:
        raise SystemExit(code)
