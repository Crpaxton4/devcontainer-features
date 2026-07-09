"""Console entry point for the standalone Odoo MCP server.

Running ``odoo-mcp`` or ``python -m odoo_sdk.mcp`` starts a server exposing the
SDK's built-in commands. Settings are resolved once from the local config file
(File > Env > Default) into a :class:`LocalConfig`, which builds the
:class:`OdooClient` and is injected — alongside the :class:`LocalStateClient` —
into every command via the :class:`Registry`.

Consumers who want to expose custom commands should build their own
:class:`Registry`, register their commands, and start :class:`OdooMCPServer`
from their own script instead of using this entry point.
"""

from odoo_sdk.client import OdooClient
from odoo_sdk.commands import Registry
from odoo_sdk.commands.builtin import register_builtins
from odoo_sdk.mcp.server import OdooMCPServer
from odoo_sdk.mcp.tools import build_explicit_tools


def main() -> None:
    """Build the default registry and run the MCP server over stdio.

    :return: None.
    :rtype: None
    """

    client = OdooClient()
    registry = register_builtins(Registry(client))
    OdooMCPServer(registry, explicit_tools=build_explicit_tools(registry)).run()


if __name__ == "__main__":
    main()
