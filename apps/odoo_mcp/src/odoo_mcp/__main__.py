"""Console entry point for the standalone Odoo MCP server.

Running ``odoo-mcp`` or ``python -m odoo_mcp`` starts a server exposing the
SDK's built-in commands. Settings are resolved once from the local config file
(File > Env > Default) into a :class:`LocalConfig`, and the object graph is
assembled by the single composition root
(:func:`odoo_mcp.bootstrap.bootstrap`, #716), which returns the
:class:`~odoo_dev_core.registry.CommandService` the tools execute through.
"""

from .bootstrap import LocalConfig, assert_configured, bootstrap
from odoo_mcp.server import OdooMCPServer
from odoo_mcp.tools import build_explicit_tools


def main() -> None:
    """Bootstrap the default command service and run the MCP server over stdio.

    Per-call profiling is resolved from the ``[behavior] profiling`` config
    setting and the ``ODOO_PROFILING`` environment variable (File > Env >
    Default) via :class:`LocalConfig`, then passed to the server.

    The server exposes every explicit tool.

    :return: None.
    :rtype: None
    """

    assert_configured()
    config = LocalConfig.load()
    service = bootstrap(config=config)
    OdooMCPServer(
        service,
        explicit_tools=build_explicit_tools(service),
        profiling=config.profiling,
    ).run()


if __name__ == "__main__":
    main()
