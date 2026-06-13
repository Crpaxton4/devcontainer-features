from typing import Any, Optional

from fastmcp import FastMCP

from odoo_sdk.client.client import OdooClient
from odoo_sdk.commands import Registry


class OdooMCPServer:
    """
    Dynamically exposes Odoo SDK Commands as MCP Tools via FastMCP.
    """

    def __init__(
        self,
        registry: Registry,
        client: Optional[OdooClient] = None,
        server_name: str = "Odoo MCP Server",
    ):
        # Implicitly initialize client using default settings/ini if one is not provided.
        # This will fail fast if the environment configuration is invalid.
        self.client = client
        self.registry: Registry = registry
        self.mcp: FastMCP = FastMCP(
            server_name,
            instructions="Provides tools for interacting with Odoo ERP",
        )
        self._bootstrap_tools()

    def _bootstrap_tools(self) -> None:
        """
        Iterates over the registry, instantiates commands, registers them as internal FastMCP tools,
        and applies ToolTransforms to cleanly expose their configured names and descriptions.
        """
        for command in self.registry:
            # Instantiate the command using the configured client
            command = command(client=self.client)

            @self.mcp.tool(name=command.name, description=command.description)
            def _command_tool(*args: Any, **kwargs: Any) -> Any:
                return command.execute(*args, **kwargs)

            # Register the tool with FastMCP under its internal name
            self.mcp.add_tool(_command_tool)

    def run(self) -> None:
        """Starts the FastMCP server."""
        self.mcp.run()
