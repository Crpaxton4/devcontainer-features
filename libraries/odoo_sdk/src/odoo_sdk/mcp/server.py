import asyncio
import functools
import inspect
from typing import Any

from fastmcp import FastMCP
from fastmcp.tools.tool import Tool

from odoo_sdk.commands import Command, Registry


class OdooMCPServer:
    """Dynamically expose the commands in a :class:`Registry` as MCP tools.

    Every command registered in the supplied registry becomes a FastMCP tool at
    construction time. The tool's name is the registration key, its description
    comes from the command's ``description`` (falling back to the ``execute``
    docstring), and its input schema is introspected from the command's
    ``execute`` signature so agents receive a fully typed tool definition.

    The registry already owns the shared :class:`OdooClient` and instantiates
    each command with it, so the server does not need a separate client.

    :param registry: Registry whose commands are exposed as tools.
    :type registry: Registry
    :param server_name: Human-readable name advertised by the MCP server.
    :type server_name: str
    """

    def __init__(
        self,
        registry: Registry,
        server_name: str = "Odoo MCP Server",
    ):
        from odoo_sdk.mcp.prompts import register_builtin_prompts

        self.registry: Registry = registry
        self.mcp: FastMCP = FastMCP(
            server_name,
            instructions="Provides tools for interacting with Odoo ERP",
        )
        self._bootstrap_tools()
        register_builtin_prompts(self.mcp, self.registry)

    def _bootstrap_tools(self) -> None:
        """Register every command in the registry as an MCP tool.

        Each command is handled by :meth:`_register_command` so the tool wrapper
        closes over a per-iteration binding instead of a shared loop variable.

        :return: None.
        :rtype: None
        """

        for name, command in self.registry.items():
            self._register_command(name, command)

    def _register_command(self, name: str, command: Command) -> None:
        """Build a typed tool for ``command`` and add it to the server.

        :param name: Public tool name (the registry registration key).
        :type name: str
        :param command: Command instance bound to the shared client.
        :type command: Command
        :return: None.
        :rtype: None
        """

        self.mcp.add_tool(self._build_tool(name, command))

    @staticmethod
    def _build_tool(name: str, command: Command) -> Tool:
        """Create a FastMCP tool mirroring ``command.execute``.

        The wrapper carries the bound ``execute`` signature via
        ``__signature__`` so FastMCP introspects the real, typed parameters
        (rather than the variadic wrapper) when generating the input schema.

        :param name: Public tool name (the registry registration key).
        :type name: str
        :param command: Command instance bound to the shared client.
        :type command: Command
        :return: A FastMCP tool that delegates to ``command.execute``.
        :rtype: Tool
        """

        execute = command.execute

        if asyncio.iscoroutinefunction(execute):
            @functools.wraps(execute)
            async def tool_fn(*args: Any, **kwargs: Any) -> Any:
                return await command.execute(*args, **kwargs)
        else:
            @functools.wraps(execute)
            def tool_fn(*args: Any, **kwargs: Any) -> Any:
                return command.execute(*args, **kwargs)

        tool_fn.__signature__ = inspect.signature(execute)
        description = command.description or inspect.getdoc(execute)
        return Tool.from_function(tool_fn, name=name, description=description)

    def run(self, **kwargs: Any) -> None:
        """Start the FastMCP server.

        :param kwargs: Transport options forwarded to ``FastMCP.run`` (defaults
            to stdio when none are given).
        :return: None.
        :rtype: None
        """

        self.mcp.run(**kwargs)
