"""Console entry point for the btop-style Odoo session TUI.

Running ``odoo-tui`` or ``python -m odoo_client.tui`` opens the Textual TUI that
explores global sessions over a date window. It receives the same default
command registry the MCP server uses from the single composition root
(:func:`odoo_client.bootstrap.bootstrap`, #716) — the client, the local state
store, and the resolved config are injected once and shared with every command
the TUI composes (``query_sessions`` for the timeline).

Consumers who want a custom command surface should build their own
:class:`~odoo_client.commands.Registry`, wrap it (with the client, store, and
config) in a :class:`~odoo_client.tui.app.TuiDeps`, and call
:func:`odoo_client.tui.app.run` directly instead of using this entry point.
"""

from odoo_client.bootstrap import (
    LocalConfig,
    assert_configured,
    bootstrap,
    build_client,
)

from .app import TuiDeps, run


def main() -> None:  # pragma: no cover
    """Bootstrap the default registry and dependencies, then run the Textual TUI.

    The client, the local state store, and the resolved config are created once
    and injected — into the registry (wired by :func:`~odoo_client.bootstrap.
    bootstrap` and shared with every command) and into the
    :class:`~odoo_client.tui.app.TuiDeps` the driver receives — so the TUI never
    harvests them off command instances. Resolving ``registry.state_client``
    caches the shared store on the registry, so both the driver and the commands
    it dispatches read and write the one store.
    """
    assert_configured()
    config = LocalConfig.load()
    client = build_client(config)
    registry = bootstrap(client=client, config=config)
    deps = TuiDeps(
        registry=registry,
        client=client,
        store=registry.state_client,
        config=config,
    )
    run(deps)


if __name__ == "__main__":
    main()
