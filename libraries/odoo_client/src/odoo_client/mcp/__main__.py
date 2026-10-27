"""Console entry point for the standalone Odoo MCP server.

Running ``odoo-mcp`` or ``python -m odoo_client.mcp`` starts a server exposing the
SDK's built-in commands. Settings are resolved once from the local config file
(File > Env > Default) into a :class:`LocalConfig`, and the object graph —
client, state, config, registry — is assembled by the single composition root
(:func:`odoo_client.bootstrap.bootstrap`, #716), which injects the resolved
config into every command via the :class:`~odoo_client.commands.Registry`.

Consumers who want to expose custom commands should build their own
:class:`~odoo_client.commands.Registry`, register their commands, and start
:class:`OdooMCPServer` from their own script instead of using this entry point.
"""

from odoo_client.bootstrap import LocalConfig, OdooClient, assert_configured, bootstrap
from odoo_client.mcp.server import OdooMCPServer
from odoo_client.mcp.tools import build_explicit_tools


def main() -> None:
    """Bootstrap the default registry and run the MCP server over stdio.

    Per-call profiling is resolved from the ``[behavior] profiling`` config
    setting and the ``ODOO_PROFILING`` environment variable (File > Env >
    Default) via :class:`LocalConfig`, then passed to the server.

    The server exposes every explicit tool.

    :return: None.
    :rtype: None
    """

    assert_configured()
    config = LocalConfig.load()
    registry = bootstrap(client=OdooClient(), config=config)
    OdooMCPServer(
        registry,
        explicit_tools=build_explicit_tools(registry),
        profiling=config.profiling,
    ).run()


if __name__ == "__main__":
    main()
