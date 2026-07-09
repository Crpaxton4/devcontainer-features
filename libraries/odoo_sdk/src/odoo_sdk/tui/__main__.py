"""Console entry point for the btop-style Odoo session TUI.

Running ``odoo-tui`` or ``python -m odoo_sdk.tui`` opens the curses TUI that
explores global sessions over a date window. It builds the same default command
registry the MCP server uses — the client, the local state store, and the
resolved config are injected once and shared with every command the TUI composes
(``query_sessions`` for the timeline, ``start_task`` / ``stop_task`` for upload).

Consumers who want a custom command surface should build their own
:class:`~odoo_sdk.commands.Registry` and call :func:`odoo_sdk.tui.app.run`
directly instead of using this entry point.
"""

from odoo_sdk.client import OdooClient
from odoo_sdk.commands import Registry
from odoo_sdk.commands.builtin import register_builtins
from odoo_sdk.state.config import LocalConfig

from .app import run


def main() -> None:  # pragma: no cover
    """Build the default registry and run the curses TUI.

    :return: None.
    :rtype: None
    """
    config = LocalConfig.load()
    client = OdooClient()
    registry = register_builtins(Registry(client, config=config))
    run(registry)


if __name__ == "__main__":
    main()
