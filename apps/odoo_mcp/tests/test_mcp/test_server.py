import asyncio
import unittest
from unittest.mock import MagicMock, Mock, patch

from odoo_mcp.server import OdooMCPServer
from ..support import make_service, wire_tools

INSTRUCTIONS = "Provides tools for interacting with Odoo ERP"


def _registry():
    return make_service(client=Mock())


def _build_with_mock_mcp(service):
    """Build a server with FastMCP patched out; capture the tools added."""
    mock_mcp = MagicMock()
    added = []
    mock_mcp.add_tool.side_effect = added.append
    with patch("odoo_mcp.server.FastMCP", return_value=mock_mcp):
        server = OdooMCPServer(service)
    return server, mock_mcp, added


class TestServerConstruction(unittest.TestCase):
    def test_stores_service(self):
        service = _registry()
        with patch("odoo_mcp.server.FastMCP"):
            server = OdooMCPServer(service)
        self.assertIs(server.service, service)

    def test_creates_fastmcp_with_name_and_instructions(self):
        with patch("odoo_mcp.server.FastMCP") as MockFastMCP:
            OdooMCPServer(_registry(), name="Custom Name")
        MockFastMCP.assert_called_once_with("Custom Name", instructions=INSTRUCTIONS)

    def test_default_server_name(self):
        with patch("odoo_mcp.server.FastMCP") as MockFastMCP:
            OdooMCPServer(_registry())
        MockFastMCP.assert_called_once_with(
            "Odoo MCP Server", instructions=INSTRUCTIONS
        )

    def test_custom_instructions_override(self):
        with patch("odoo_mcp.server.FastMCP") as MockFastMCP:
            OdooMCPServer(_registry(), instructions="Serves a widget registry")
        MockFastMCP.assert_called_once_with(
            "Odoo MCP Server", instructions="Serves a widget registry"
        )


class TestToolRegistration(unittest.TestCase):
    def test_registers_one_tool_per_mcp_command(self):
        service = _registry()
        _, mock_mcp, added = _build_with_mock_mcp(service)
        expected = {spec.id for spec in service.registry.specs("mcp")}
        self.assertEqual(mock_mcp.add_tool.call_count, 32)
        self.assertEqual({t.name for t in added}, expected)

    def test_non_mcp_commands_are_not_registered(self):
        _, _, added = _build_with_mock_mcp(_registry())
        names = {t.name for t in added}
        self.assertFalse(names & {"log_event", "upload", "get_employee_id"})

    def test_description_comes_from_the_spec(self):
        service = _registry()
        _, _, added = _build_with_mock_mcp(service)
        for tool in added:
            self.assertEqual(
                tool.description, service.registry.get(tool.name).description
            )

    def test_registered_tool_dispatches_to_the_command(self):
        client = Mock()
        client.uid = 99
        _, _, added = _build_with_mock_mcp(make_service(client=client))
        tools = {t.name: t for t in added}
        self.assertEqual(tools["get_uid"].fn(), 99)

    def test_generated_tools_are_sync(self):
        _, _, added = _build_with_mock_mcp(_registry())
        self.assertFalse(any(asyncio.iscoroutinefunction(t.fn) for t in added))

    def test_wrapper_chain_keeps_the_schema(self):
        _, _, added = _build_with_mock_mcp(_registry())
        note = {t.name: t for t in added}["task_note"]
        self.assertEqual(
            set(note.parameters["properties"]),
            {"task_id", "note", "attachments", "dedupe_key"},
        )
        self.assertEqual(note.parameters["required"], ["task_id", "note"])


class TestFakeToolChain(unittest.TestCase):
    """The wrapper chain accepts sync and async callables with a plain signature."""

    def test_schema_from_signature_and_description_from_pair(self):
        def echo(message: str, shout: bool = False) -> str:
            return message.upper() if shout else message

        tools = wire_tools(_registry(), {"echo": (echo, "Echo a message back.")})
        tool = tools["echo"]
        self.assertEqual(tool.description, "Echo a message back.")
        self.assertEqual(set(tool.parameters["properties"]), {"message", "shout"})
        self.assertEqual(tool.parameters["required"], ["message"])
        self.assertEqual(tool.fn(message="hi", shout=True), "HI")

    def test_description_falls_back_to_docstring(self):
        def impl() -> str:
            """Docstring description."""
            return "x"

        self.assertEqual(
            wire_tools(_registry(), {"echo": impl})["echo"].description,
            "Docstring description.",
        )

    def test_async_tool_registered_as_coroutine(self):
        async def async_cmd(value: int) -> int:
            """Async tool."""
            return value * 2

        tool = wire_tools(_registry(), {"async_cmd": async_cmd})["async_cmd"]
        self.assertTrue(asyncio.iscoroutinefunction(tool.fn))
        self.assertEqual(asyncio.run(tool.fn(value=5)), 10)


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
