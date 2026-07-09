from typing import Any, Callable, Optional, Tuple, Union

from fastmcp import FastMCP
from fastmcp.tools.tool import Tool

from odoo_sdk.commands import Registry

# A tool spec is either a bare callable, or a ``(callable, description)`` pair.
ToolSpec = Union[Callable[..., Any], Tuple[Callable[..., Any], str]]


class OdooMCPServer:
    """Expose a set of explicit MCP tools built from a command :class:`Registry`.

    Every tool is defined explicitly in :mod:`odoo_sdk.mcp.tools` — one function
    per tool with a real, typed signature that delegates to a command. The server
    performs no auto-reflection of command ``execute`` signatures: the tool
    surface is exactly what ``explicit_tools`` provides. This keeps the wire
    schema an intentional part of the interaction surface and lets composition
    tools (which take the FastMCP ``ctx``) coexist with atomic tools uniformly.

    :param registry: Registry that owns the shared command dependencies. It is
        retained for prompt registration and is the registry the explicit tools
        compose.
    :type registry: Registry
    :param server_name: Human-readable name advertised by the MCP server.
    :type server_name: str
    :param explicit_tools: Mapping of tool name to either a tool callable or a
        ``(callable, description)`` pair. When omitted, the server exposes no
        tools (only prompts), defaults to None.
    :type explicit_tools: Optional[dict[str, ToolSpec]]
    """

    def __init__(
        self,
        registry: Registry,
        server_name: str = "Odoo MCP Server",
        explicit_tools: Optional[dict[str, ToolSpec]] = None,
    ):
        from odoo_sdk.mcp.prompts import register_builtin_prompts

        self.registry: Registry = registry
        self._explicit_tools: dict[str, ToolSpec] = explicit_tools or {}
        self.mcp: FastMCP = FastMCP(
            server_name,
            instructions="Provides tools for interacting with Odoo ERP",
        )
        self._register_tools()
        register_builtin_prompts(self.mcp, self.registry)

    def _register_tools(self) -> None:
        """Register each explicit tool with the FastMCP server.

        :return: None.
        :rtype: None
        """

        for name, spec in self._explicit_tools.items():
            tool_fn, description = self._unpack_spec(spec)
            self.mcp.add_tool(
                Tool.from_function(tool_fn, name=name, description=description or None)
            )

    @staticmethod
    def _unpack_spec(spec: ToolSpec) -> Tuple[Callable[..., Any], str]:
        """Normalize a tool spec into a ``(callable, description)`` pair.

        :param spec: Bare callable or ``(callable, description)`` pair.
        :type spec: ToolSpec
        :return: The tool callable and its description (``""`` when absent).
        :rtype: Tuple[Callable[..., Any], str]
        """

        if isinstance(spec, tuple):
            tool_fn, description = spec
            return tool_fn, description
        return spec, ""

    def run(self, **kwargs: Any) -> None:
        """Start the FastMCP server.

        :param kwargs: Transport options forwarded to ``FastMCP.run`` (defaults
            to stdio when none are given).
        :return: None.
        :rtype: None
        """

        self.mcp.run(**kwargs)
