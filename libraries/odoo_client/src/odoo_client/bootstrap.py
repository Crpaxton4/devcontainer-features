"""Single composition root for the Odoo SDK object graph (#716, ADR-005).

Before this module existed, the three surface entrypoints
(:mod:`odoo_client.cli.__main__`, :mod:`odoo_client.mcp.__main__`,
:mod:`odoo_client.tui.__main__`) each constructed their own
:class:`~odoo_client.client.OdooClient` / :class:`~odoo_client.state.LocalConfig` /
:class:`~odoo_client.state.LocalStateClient` and wired their own
:class:`~odoo_client.commands.Registry` — three drifting object graphs.
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
:class:`LocalStateClient`) are re-exported here as part of the composition
surface: an entrypoint that must build a piece of the graph itself — the CLI's
lazy client wrapper, the resolved config whose values the entrypoint reads
directly (MCP profiling, CLI upload policy) — obtains the constructor from the
composition root instead of importing the data layer, keeping ``bootstrap``
the entrypoints' only below-core dependency.
"""

from typing import Optional

from odoo_client.client import OdooClient
from odoo_client.commands import Registry
from odoo_client.commands.builtin import register_builtins
from odoo_client.commands.protocols import RpcClient
from odoo_client.state import LocalConfig, LocalStateClient, assert_tracker_db_present

__all__ = [
    "assert_configured",
    "bootstrap",
    "OdooClient",
    "LocalConfig",
    "LocalStateClient",
]


def bootstrap(
    *,
    client: Optional[RpcClient] = None,
    state: Optional[LocalStateClient] = None,
    config: Optional[LocalConfig] = None,
) -> Registry:
    """Assemble the default SDK object graph and return the wired registry.

    Every omitted peer resolves to the production default, so the plain
    ``bootstrap()`` call builds the exact graph the entrypoints ship, while an
    explicit argument swaps in a caller-owned instance (the CLI's lazy client
    wrapper, a test fake) without touching the wiring:

    * ``client`` — any :class:`~odoo_client.commands.protocols.RpcClient`;
      defaults to a fresh :class:`OdooClient` built from the resolved
      connection settings.
    * ``state`` — the shared :class:`LocalStateClient`; left ``None`` by
      default so the :class:`~odoo_client.commands.Registry` resolves it lazily
      on first use and merely building a graph never forces the SQLite
      tracker DB into existence (the MCP server relies on this).
    * ``config`` — the resolved :class:`LocalConfig`; defaults to one
      ``LocalConfig.load()`` here, the single production load site (#716).
      The loaded config is injected into every command via the registry, so
      the command layer never resolves configuration on its own.

    :param client: RPC client shared with every registered command.
    :param state: Shared local state client, or ``None`` for lazy resolution.
    :param config: Resolved SDK settings, or ``None`` to load them once here.
    :returns: A :class:`~odoo_client.commands.Registry` with all built-in
        commands registered and the shared peers injected.
    """
    if client is None:
        client = OdooClient()
    if config is None:
        config = LocalConfig.load()
    return register_builtins(Registry(client, state_client=state, config=config))


def assert_configured() -> None:
    """Raise if the entrypoint's real preconditions are unmet (#642).

    What the commands need is resolvable Odoo connection settings and the
    tracker DB, so those are what is checked. Both failures already have a
    named, actionable error that the MCP boundary
    (:data:`~odoo_client.mcp.server._BOUNDARY_ERRORS`) renders as a structured
    payload, so no new exception type is introduced.

    Only the *settings* are resolved — no :class:`~odoo_client.client.OdooClient`
    is built and no socket is opened — so the lazy-client contract holds.

    :raises ValueError: When required Odoo connection settings are unresolved.
    :raises TrackerStateMissingError: When the central tracker DB is absent.
    """
    LocalConfig.load().connection_settings()
    assert_tracker_db_present()
