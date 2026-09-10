"""Console entry point for the standalone Odoo MCP server.

Running ``odoo-mcp`` or ``python -m odoo_sdk.mcp`` starts a server exposing the
SDK's built-in commands. Settings are resolved once from the local config file
(File > Env > Default) into a :class:`LocalConfig`, and the object graph —
client, state, config, registry — is assembled by the single composition root
(:func:`odoo_sdk.bootstrap.bootstrap`, #716), which injects the resolved
config into every command via the :class:`~odoo_sdk.commands.Registry`.

Consumers who want to expose custom commands should build their own
:class:`~odoo_sdk.commands.Registry`, register their commands, and start
:class:`OdooMCPServer` from their own script instead of using this entry point.
"""

from odoo_sdk.bootstrap import LocalConfig, OdooClient, bootstrap
from odoo_sdk.mcp.server import OdooMCPServer
from odoo_sdk.mcp.tools import build_explicit_tools, default_tool_surface


def main() -> None:
    """Bootstrap the default registry and run the MCP server over stdio.

    Per-call profiling is resolved from the ``[behavior] profiling`` config
    setting and the ``ODOO_PROFILING`` environment variable (File > Env >
    Default) via :class:`LocalConfig`, then passed to the server.

    The server exposes the default tool surface — the everyday working set —
    with the narrow-context tools held back so the count stays under Claude
    Code's client-side lazy-deferral threshold (#512). Setting
    ``ODOO_MCP_INCLUDE_GATED`` restores the full surface for a session that
    needs the maintenance/triage tooling. The gate itself is native fastmcp
    visibility since #715 — :class:`OdooMCPServer` tags gated tools and
    disables the tag unless the opt-in is set — while this entry point still
    applies the (deprecated) :func:`default_tool_surface` pre-filter, whose
    hand-off is pinned by the frozen surface contract tests; both layers
    resolve the same env flag, so the client-observable surface is identical.

    :return: None.
    :rtype: None
    """

    config = LocalConfig.load()
    registry = bootstrap(client=OdooClient(), config=config)
    OdooMCPServer(
        registry,
        explicit_tools=default_tool_surface(build_explicit_tools(registry)),
        profiling=config.profiling,
    ).run()


if __name__ == "__main__":
    main()
