import asyncio
import unittest
from unittest.mock import MagicMock, Mock, patch

from odoo_sdk.commands.command_registry import Registry
from odoo_sdk.mcp.server import OdooMCPServer

INSTRUCTIONS = "Provides tools for interacting with Odoo ERP"


def _registry():
    return Registry(Mock())


def _build_with_mock_mcp(registry, explicit_tools=None):
    """Build a server with FastMCP patched out; capture the tools added."""
    mock_mcp = MagicMock()
    added = []
    mock_mcp.add_tool.side_effect = added.append
    with patch("odoo_sdk.mcp.server.FastMCP", return_value=mock_mcp):
        server = OdooMCPServer(registry, explicit_tools=explicit_tools)
    return server, mock_mcp, added


class TestServerConstruction(unittest.TestCase):
    def test_stores_registry(self):
        registry = _registry()
        with patch("odoo_sdk.mcp.server.FastMCP"):
            server = OdooMCPServer(registry)
        self.assertIs(server.registry, registry)

    def test_creates_fastmcp_with_name_and_instructions(self):
        with patch("odoo_sdk.mcp.server.FastMCP") as MockFastMCP:
            OdooMCPServer(_registry(), server_name="Custom Name")
        MockFastMCP.assert_called_once_with("Custom Name", instructions=INSTRUCTIONS)

    def test_default_server_name(self):
        with patch("odoo_sdk.mcp.server.FastMCP") as MockFastMCP:
            OdooMCPServer(_registry())
        MockFastMCP.assert_called_once_with(
            "Odoo MCP Server", instructions=INSTRUCTIONS
        )

    def test_custom_instructions_override(self):
        with patch("odoo_sdk.mcp.server.FastMCP") as MockFastMCP:
            OdooMCPServer(_registry(), instructions="Serves a widget registry")
        MockFastMCP.assert_called_once_with(
            "Odoo MCP Server", instructions="Serves a widget registry"
        )


class TestExplicitToolRegistration(unittest.TestCase):
    def test_no_explicit_tools_registers_no_tools(self):
        _, mock_mcp, _ = _build_with_mock_mcp(_registry())
        mock_mcp.add_tool.assert_not_called()

    def test_registers_one_tool_per_explicit_tool(self):
        def a() -> str:
            """Tool A."""
            return "A-result"

        def b() -> str:
            """Tool B."""
            return "B-result"

        _, mock_mcp, added = _build_with_mock_mcp(
            _registry(), explicit_tools={"a": a, "b": b}
        )
        self.assertEqual(mock_mcp.add_tool.call_count, 2)
        self.assertEqual(sorted(t.name for t in added), ["a", "b"])

    def test_tool_name_comes_from_mapping_key(self):
        def impl(message: str, shout: bool = False) -> str:
            """Echo a message back."""
            return message.upper() if shout else message

        _, _, added = _build_with_mock_mcp(
            _registry(), explicit_tools={"public_name": impl}
        )
        self.assertEqual(added[0].name, "public_name")

    def test_description_from_spec_pair(self):
        def impl() -> str:
            return "x"

        _, _, added = _build_with_mock_mcp(
            _registry(), explicit_tools={"echo": (impl, "Echo a message back.")}
        )
        self.assertEqual(added[0].description, "Echo a message back.")

    def test_description_falls_back_to_docstring(self):
        def impl() -> str:
            """Docstring description."""
            return "x"

        _, _, added = _build_with_mock_mcp(
            _registry(), explicit_tools={"echo": impl}
        )
        self.assertEqual(added[0].description, "Docstring description.")

    def test_schema_from_explicit_signature(self):
        def echo(message: str, shout: bool = False) -> str:
            """Echo a message back."""
            return message.upper() if shout else message

        _, _, added = _build_with_mock_mcp(
            _registry(), explicit_tools={"echo": echo}
        )
        params = added[0].parameters
        self.assertEqual(set(params["properties"]), {"message", "shout"})
        self.assertEqual(params["required"], ["message"])
        self.assertEqual(params["properties"]["message"]["type"], "string")

    def test_each_tool_routes_to_its_own_callable(self):
        def a() -> str:
            """A."""
            return "A-result"

        def b() -> str:
            """B."""
            return "B-result"

        _, _, added = _build_with_mock_mcp(
            _registry(), explicit_tools={"a": a, "b": b}
        )
        tools = {tool.name: tool for tool in added}
        self.assertEqual(tools["a"].fn(), "A-result")
        self.assertEqual(tools["b"].fn(), "B-result")

    def test_tool_invokes_callable_with_arguments(self):
        def echo(message: str, shout: bool = False) -> str:
            """Echo."""
            return message.upper() if shout else message

        _, _, added = _build_with_mock_mcp(
            _registry(), explicit_tools={"echo": echo}
        )
        self.assertEqual(added[0].fn(message="hi", shout=True), "HI")

    def test_async_tool_registered_as_coroutine(self):
        async def async_cmd(value: int) -> int:
            """Async tool."""
            return value * 2

        _, _, added = _build_with_mock_mcp(
            _registry(), explicit_tools={"async_cmd": async_cmd}
        )
        self.assertTrue(asyncio.iscoroutinefunction(added[0].fn))
        self.assertEqual(asyncio.run(added[0].fn(value=5)), 10)

    def test_sync_and_async_tools_registered_independently(self):
        async def async_cmd(value: int) -> int:
            """Async."""
            return value * 2

        def sync_cmd(value: int) -> int:
            """Sync."""
            return value + 1

        _, _, added = _build_with_mock_mcp(
            _registry(),
            explicit_tools={"async_cmd": async_cmd, "sync_cmd": sync_cmd},
        )
        names = {t.name for t in added}
        self.assertEqual(names, {"async_cmd", "sync_cmd"})


class TestRun(unittest.TestCase):
    def test_run_delegates_to_mcp(self):
        server, mock_mcp, _ = _build_with_mock_mcp(_registry())
        server.run()
        mock_mcp.run.assert_called_once_with()

    def test_run_forwards_transport_kwargs(self):
        server, mock_mcp, _ = _build_with_mock_mcp(_registry())
        server.run(transport="stdio", show_banner=False)
        mock_mcp.run.assert_called_once_with(transport="stdio", show_banner=False)


if __name__ == "__main__":
    unittest.main()
