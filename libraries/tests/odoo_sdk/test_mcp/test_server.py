import unittest
from unittest.mock import MagicMock, Mock, patch

from odoo_sdk.commands import Command
from odoo_sdk.commands.command_registry import Registry
from odoo_sdk.mcp.server import OdooMCPServer

INSTRUCTIONS = "Provides tools for interacting with Odoo ERP"


class EchoCommand(Command):
    """Echo a message back, optionally shouting it."""

    _name = "internal_echo"
    _description = "Echo a message back."

    def execute(self, message: str, shout: bool = False) -> str:
        return message.upper() if shout else message


class CmdA(Command):
    _name = "a"
    _description = "A"

    def execute(self) -> str:
        return "A-result"


class CmdB(Command):
    _name = "b"
    _description = "B"

    def execute(self) -> str:
        return "B-result"


def _registry(**commands):
    registry = Registry(Mock())
    for name, command in commands.items():
        registry.register(name, command)
    return registry


def _build_with_mock_mcp(registry):
    """Build a server with FastMCP patched out; capture the tools added."""
    mock_mcp = MagicMock()
    added = []
    mock_mcp.add_tool.side_effect = added.append
    with patch("odoo_sdk.mcp.server.FastMCP", return_value=mock_mcp):
        server = OdooMCPServer(registry)
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


class TestBootstrapTools(unittest.TestCase):
    def test_registers_one_tool_per_command(self):
        registry = _registry(a=CmdA, b=CmdB)
        _, mock_mcp, _ = _build_with_mock_mcp(registry)
        self.assertEqual(mock_mcp.add_tool.call_count, 2)

    def test_empty_registry_registers_no_tools(self):
        _, mock_mcp, _ = _build_with_mock_mcp(_registry())
        mock_mcp.add_tool.assert_not_called()

    def test_tool_name_comes_from_registry_key(self):
        # Registration key differs from the command's _name.
        registry = _registry(public_name=EchoCommand)
        _, _, added = _build_with_mock_mcp(registry)
        self.assertEqual(added[0].name, "public_name")

    def test_tool_description_from_command(self):
        registry = _registry(echo=EchoCommand)
        _, _, added = _build_with_mock_mcp(registry)
        self.assertEqual(added[0].description, "Echo a message back.")

    def test_tool_schema_introspected_from_execute(self):
        registry = _registry(echo=EchoCommand)
        _, _, added = _build_with_mock_mcp(registry)
        params = added[0].parameters
        self.assertEqual(set(params["properties"]), {"message", "shout"})
        self.assertEqual(params["required"], ["message"])
        self.assertEqual(params["properties"]["message"]["type"], "string")

    def test_each_tool_routes_to_its_own_command(self):
        # Regression guard for the closure-capture bug where every tool ran the
        # last registered command.
        registry = _registry(a=CmdA, b=CmdB)
        _, _, added = _build_with_mock_mcp(registry)
        tools = {tool.name: tool for tool in added}
        self.assertEqual(tools["a"].fn(), "A-result")
        self.assertEqual(tools["b"].fn(), "B-result")

    def test_tool_invokes_command_with_arguments(self):
        registry = _registry(echo=EchoCommand)
        _, _, added = _build_with_mock_mcp(registry)
        self.assertEqual(added[0].fn(message="hi", shout=True), "HI")

    def test_command_instantiated_with_registry_client(self):
        seen = []

        class TrackingCommand(Command):
            _name = "tracked"
            _description = "tracked"

            def __init__(self, client=None):
                super().__init__(client)
                seen.append(self._client)

            def execute(self) -> str:
                return "ok"

        client = Mock()
        registry = Registry(client)
        registry.register("tracked", TrackingCommand)
        _build_with_mock_mcp(registry)
        self.assertIn(client, seen)


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
