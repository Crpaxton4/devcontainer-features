"""Console entry point for the btop-style Odoo session TUI.

Running ``odoo-tui`` or ``python -m odoo_tui`` opens the Textual TUI that
explores global sessions over a date window. It receives the same default
command service the MCP server uses from the single composition root
(:func:`odoo_tui.bootstrap.bootstrap`, #716) - the client, the local state
store, and the resolved config are injected once and shared with every command
the TUI composes (``query_sessions`` for the timeline).

Consumers who want a custom command surface should build their own
:class:`~odoo_dev_core.registry.CommandService`, wrap it in a
:class:`~odoo_tui.app.TuiDeps`, and call :func:`odoo_tui.app.run` directly
instead of using this entry point.
"""

from .bootstrap import (
    LocalConfig,
    assert_configured,
    bootstrap,
    build_client,
)

from .app import TuiDeps, run


def main() -> None:  # pragma: no cover
    """Bootstrap the default command service, then run the Textual TUI.

    The client, the local state store, and the resolved config are created once
    by :func:`~odoo_tui.bootstrap.bootstrap` and shared by the driver and every
    command it dispatches, so both read and write the one store.
    """
    assert_configured()
    config = LocalConfig.load()
    client = build_client(config)
    service = bootstrap(client=client, config=config)
    run(TuiDeps(service=service))


if __name__ == "__main__":
    main()
