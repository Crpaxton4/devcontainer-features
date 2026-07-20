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
from odoo_sdk.mcp.tools import build_explicit_tools, default_tool_surface
from odoo_sdk.state.config import LocalConfig


def main() -> None:
    """Build the default registry and run the MCP server over stdio.

    Per-call profiling is resolved from the ``[behavior] profiling`` config
    setting and the ``ODOO_PROFILING`` environment variable (File > Env >
    Default) via :class:`LocalConfig`, then passed to the server.

    The server exposes the default tool surface — the everyday working set — via
    :func:`default_tool_surface`, which holds back the narrow-context tools so the
    count stays under Claude Code's client-side lazy-deferral threshold (#512).
    Setting ``ODOO_MCP_INCLUDE_GATED`` restores the full surface for a session
    that needs the maintenance/triage tooling.

    :return: None.
    :rtype: None
    """

    config = LocalConfig.load()
    client = OdooClient()
    registry = register_builtins(Registry(client))
    OdooMCPServer(
        registry,
        explicit_tools=default_tool_surface(build_explicit_tools(registry)),
        profiling=config.profiling,
    ).run()


if __name__ == "__main__":
    main()
