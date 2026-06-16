import unittest
from unittest.mock import MagicMock, Mock, patch

from odoo_sdk.commands.command_registry import Registry
from odoo_sdk.mcp.server import OdooMCPServer


class ConcreteCommand:
    _name = "test_command"
    _description = "A test command"

    def __init__(self, client=None):
        self._client = client

    @property
    def name(self):
        return self._name

    @property
    def description(self):
        return self._description

    def execute(self, *args, **kwargs):
        return "executed"


class TestOdooMCPServer(unittest.TestCase):
    def _make_registry_with_command(self):
        mock_client = Mock()
        registry = Registry(mock_client)
        registry.register("test_command", ConcreteCommand)
        return registry

    def _make_empty_registry(self):
        return Registry(Mock())

    def _make_mock_mcp(self):
        mock_mcp = MagicMock()
        mock_tool_decorator = MagicMock(return_value=lambda fn: fn)
        mock_mcp.tool.return_value = mock_tool_decorator
        return mock_mcp

    def test_server_stores_registry(self):
        registry = self._make_empty_registry()
        with patch("odoo_sdk.mcp.server.FastMCP"):
            server = OdooMCPServer(registry)
        self.assertIs(server.registry, registry)

    def test_server_client_defaults_to_none(self):
        registry = self._make_empty_registry()
        with patch("odoo_sdk.mcp.server.FastMCP"):
            server = OdooMCPServer(registry)
        self.assertIsNone(server.client)

    def test_server_stores_provided_client(self):
        mock_client = Mock()
        registry = self._make_empty_registry()
        with patch("odoo_sdk.mcp.server.FastMCP"):
            server = OdooMCPServer(registry, client=mock_client)
        self.assertIs(server.client, mock_client)

    def test_server_creates_fastmcp_instance(self):
        registry = self._make_empty_registry()
        with patch("odoo_sdk.mcp.server.FastMCP") as MockFastMCP:
            server = OdooMCPServer(registry, server_name="Test Server")
        MockFastMCP.assert_called_once_with(
            "Test Server",
            instructions="Provides tools for interacting with Odoo ERP",
        )

    def test_default_server_name(self):
        registry = self._make_empty_registry()
        with patch("odoo_sdk.mcp.server.FastMCP") as MockFastMCP:
            server = OdooMCPServer(registry)
        MockFastMCP.assert_called_once_with(
            "Odoo MCP Server",
            instructions="Provides tools for interacting with Odoo ERP",
        )

    def test_bootstrap_tools_registers_each_command(self):
        registry = self._make_registry_with_command()
        mock_mcp = self._make_mock_mcp()
        with patch("odoo_sdk.mcp.server.FastMCP", return_value=mock_mcp):
            server = OdooMCPServer(registry, client=Mock())
        mock_mcp.tool.assert_called_once_with(
            name="test_command", description="A test command"
        )
        mock_mcp.add_tool.assert_called_once()

    def test_bootstrap_tools_instantiates_command_with_client(self):
        mock_client = Mock()
        registry = Registry(mock_client)

        instantiated = []

        class TrackingCommand:
            _name = "tracked"
            _description = "Tracked"

            def __init__(self, client=None):
                self._client = client
                instantiated.append(client)

            @property
            def name(self):
                return self._name

            @property
            def description(self):
                return self._description

            def execute(self):
                return "ok"

        registry.register("tracked", TrackingCommand)
        mock_mcp = self._make_mock_mcp()
        with patch("odoo_sdk.mcp.server.FastMCP", return_value=mock_mcp):
            server = OdooMCPServer(registry, client=mock_client)
        self.assertIn(mock_client, instantiated)

    def test_run_delegates_to_mcp(self):
        registry = self._make_empty_registry()
        mock_mcp = self._make_mock_mcp()
        with patch("odoo_sdk.mcp.server.FastMCP", return_value=mock_mcp):
            server = OdooMCPServer(registry)
        server.run()
        mock_mcp.run.assert_called_once()

    def test_bootstrap_registers_multiple_commands(self):
        mock_client = Mock()
        registry = Registry(mock_client)

        class CmdA:
            _name = "a"
            _description = "A"

            def __init__(self, client=None):
                self._client = client

            @property
            def name(self):
                return self._name

            @property
            def description(self):
                return self._description

            def execute(self):
                return "a"

        class CmdB:
            _name = "b"
            _description = "B"

            def __init__(self, client=None):
                self._client = client

            @property
            def name(self):
                return self._name

            @property
            def description(self):
                return self._description

            def execute(self):
                return "b"

        registry.register("a", CmdA)
        registry.register("b", CmdB)
        mock_mcp = self._make_mock_mcp()
        with patch("odoo_sdk.mcp.server.FastMCP", return_value=mock_mcp):
            server = OdooMCPServer(registry, client=mock_client)
        self.assertEqual(mock_mcp.tool.call_count, 2)
        self.assertEqual(mock_mcp.add_tool.call_count, 2)
