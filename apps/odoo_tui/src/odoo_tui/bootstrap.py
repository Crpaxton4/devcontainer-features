"""Composition root of the ``odoo_tui`` app: the one module that names data constructors.

Before this module existed, the three surface entrypoints
(the CLI, MCP and TUI entrypoints) each constructed their own
:class:`~odoo_client.client.OdooClient` / :class:`~odoo_dev_data.config.LocalConfig` /
:class:`~odoo_dev_data.events.EventStore` and wired their own
:class:`~odoo_dev_core.commands.Registry` — three drifting object graphs.
:func:`bootstrap` is now the ONE place the default graph is assembled: every
entrypoint shrinks to *parse → bootstrap → dispatch → format* and receives a
fully wired registry with the builtins registered and the resolved config
injected into every command.

This module is deliberately unconstrained by the ADR-005 layering contracts:
it is the composition root, the single module allowed to name every layer
(surface-adjacent wiring, core, and data) in order to connect them. In
exchange, an import-linter ``forbidden`` contract ("only the entrypoints
import the composition root") guarantees nothing else in the package imports
it, so the concrete constructors never leak back into core or the surfaces.

The concrete constructors (:class:`OdooClient`, :class:`LocalConfig`,
:class:`EventStore`) are re-exported here as part of the composition
surface: an entrypoint that must build a piece of the graph itself — the CLI's
lazy client wrapper, the resolved config whose values the entrypoint reads
directly (MCP profiling, CLI upload policy) — obtains the constructor from the
composition root instead of importing the data layer, keeping ``bootstrap``
the entrypoints' only below-core dependency.
"""

import dataclasses
from typing import Optional

from odoo_dev_data.config import LocalConfig, connection_values
from odoo_dev_data.events import EventStore

from odoo_client.client import OdooClient
from odoo_dev_core.commands import Registry
from odoo_dev_core.commands.builtin import register_builtins
from odoo_dev_core.commands.protocols import RpcClient
from odoo_client.settings import OdooConnectionSettings, build_connection_settings

__all__ = [
    "assert_configured",
    "bootstrap",
    "build_client",
    "settings_from_config",
    "OdooClient",
    "LocalConfig",
    "EventStore",
]


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
    client: Optional[RpcClient] = None,
    state: Optional[EventStore] = None,
    config: Optional[LocalConfig] = None,
) -> Registry:
    """Assemble the default SDK object graph and return the wired registry.

    Every omitted peer resolves to the production default, so the plain
    ``bootstrap()`` call builds the exact graph the entrypoints ship, while an
    explicit argument swaps in a caller-owned instance (the CLI's lazy client
    wrapper, a test fake) without touching the wiring:

    * ``client`` — any :class:`~odoo_dev_core.commands.protocols.RpcClient`;
      defaults to a fresh :class:`OdooClient` from :func:`build_client`.
    * ``state`` — the shared :class:`EventStore`; left ``None`` by default so
      the :class:`~odoo_dev_core.commands.Registry` resolves it lazily on first
      use and merely building a graph never opens ``state.db`` (the MCP server
      relies on this).
    * ``config`` — the resolved :class:`LocalConfig`; defaults to one
      ``LocalConfig.load()`` here, the single production load site (#716).
      The loaded config is injected into every command via the registry, so
      the command layer never resolves configuration on its own.

    :param client: RPC client shared with every registered command.
    :param state: Shared event store, or ``None`` for lazy resolution.
    :param config: Resolved SDK settings, or ``None`` to load them once here.
    :returns: A :class:`~odoo_dev_core.commands.Registry` with all built-in
        commands registered and the shared peers injected.
    """
    if config is None:
        config = LocalConfig.load()
    if client is None:
        client = build_client(config)
    return register_builtins(Registry(client, state_client=state, config=config))


def assert_configured() -> None:
    """Raise if the entrypoint's real precondition is unmet (#642).

    What the commands need is resolvable Odoo connection settings. The failure
    already has a named, actionable error that the MCP boundary
    (:data:`~odoo_mcp.server._BOUNDARY_ERRORS`) renders as a structured
    payload, so no new exception type is introduced.

    Only the *settings* are resolved. No :class:`~odoo_client.client.OdooClient`
    is built and no socket is opened, so the lazy-client contract holds.

    :raises ValueError: When required Odoo connection settings are unresolved.
    """
    settings_from_config(LocalConfig.load())
