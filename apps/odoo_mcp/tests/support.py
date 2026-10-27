"""Shared test helpers for opening an event store on a throwaway home.

:class:`~odoo_dev_data.events.EventStore` creates ``state.db`` under
``$ODOO_DEV_HOME`` on first connect, so a test needs only a fresh home directory.
"""

import dataclasses
import tempfile
from pathlib import Path
from typing import Any, Optional
from unittest.mock import MagicMock, patch

from fastmcp.tools import Tool
from odoo_dev_core import CommandService
from odoo_dev_data.config import LocalConfig
from odoo_dev_data.events import EventStore

from odoo_mcp.bootstrap import bootstrap
from odoo_mcp.server import OdooMCPServer


def wire_tools(
    service: CommandService,
    tools: dict[str, Any],
    *,
    profiling: bool = False,
) -> dict[str, Tool]:
    """Run fake ``tools`` through the server's wrapper chain; return the FastMCP tools.

    Each value is a callable or a ``(callable, description)`` pair. FastMCP is
    mocked out, and the tools generated from ``service`` are dropped, so only the
    fakes remain.
    """
    mock_mcp = MagicMock()
    added: list[Tool] = []
    mock_mcp.add_tool.side_effect = added.append
    with patch("odoo_mcp.server.FastMCP", return_value=mock_mcp):
        server = OdooMCPServer(service, profiling=profiling)
        added.clear()
        for name, spec in tools.items():
            fn, description = spec if isinstance(spec, tuple) else (spec, "")
            server._add_tool(name, fn, description)
    return {tool.name: tool for tool in added}


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
    """Return the real command service over the given fakes.

    Extra keyword arguments replace members of the ``Services`` bundle.
    """
    built = bootstrap(
        env={} if env is None else env,
        odoo=client if client is not None else object(),
        events=events if events is not None else make_event_store(),
        config=config if config is not None else LocalConfig(),
    )
    if not services:
        return built
    return CommandService(
        built.registry, dataclasses.replace(built.services, **services)
    )
