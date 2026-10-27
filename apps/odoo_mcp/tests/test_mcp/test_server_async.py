"""Integration tests: build_explicit_tools wired into OdooMCPServer.

Verifies the full builtin tool surface is registered explicitly (no
auto-reflection).
"""

import asyncio
import unittest
from unittest.mock import MagicMock, Mock, patch

from odoo_dev_core import CommandService, default_registry
from odoo_mcp.server import OdooMCPServer
from odoo_mcp.tools import build_explicit_tools
from ..support import make_service

#: Built-ins deliberately absent from the MCP surface: the specs that do not
#: list the ``mcp`` surface. ``get_employee_id`` is only needed by the unattended
#: timesheet-export path, ``log_event`` and ``upload`` are CLI plumbing; none has
#: an LLM in the loop.
NON_MCP_BUILTINS = {"get_employee_id", "log_event", "upload"}


def _mcp_tool_names() -> set:
    """Return the builtin names that are expected on the MCP tool surface."""
    return {spec.id for spec in default_registry().specs("mcp")}


def _full_service() -> CommandService:
    return make_service(client=Mock())


def _build(command_service, explicit_tools):
    mock_mcp = MagicMock()
    added = []
    mock_mcp.add_tool.side_effect = added.append
    with patch("odoo_mcp.server.FastMCP", return_value=mock_mcp):
        OdooMCPServer(command_service, explicit_tools=explicit_tools)
    return added


class TestFullToolSurface(unittest.TestCase):
    def test_every_builtin_command_has_an_explicit_tool(self):
        command_service = _full_service()
        tools = build_explicit_tools(command_service)
        self.assertEqual(set(tools), _mcp_tool_names())

    def test_server_registers_all_builtin_tools(self):
        command_service = _full_service()
        added = _build(command_service, build_explicit_tools(command_service))
        self.assertEqual({t.name for t in added}, _mcp_tool_names())

    def test_descriptions_sourced_from_commands(self):
        command_service = _full_service()
        added = _build(command_service, build_explicit_tools(command_service))
        by_name = {t.name for t in added if t.description}
        # Every tool has a non-empty description.
        self.assertEqual(by_name, _mcp_tool_names())

    def test_non_mcp_builtins_match_the_surface_flags(self):
        self.assertEqual(
            NON_MCP_BUILTINS, set(default_registry().ids()) - _mcp_tool_names()
        )

    def test_non_mcp_builtins_are_registered_but_not_exposed(self):
        command_service = _full_service()
        tools = build_explicit_tools(command_service)
        for name in NON_MCP_BUILTINS:
            self.assertIn(name, default_registry().ids())
            self.assertNotIn(name, tools)

    def test_atomic_tools_are_sync(self):
        command_service = _full_service()
        added = _build(command_service, build_explicit_tools(command_service))
        by_name = {t.name: t for t in added}
        self.assertFalse(asyncio.iscoroutinefunction(by_name["get_uid"].fn))
        self.assertFalse(asyncio.iscoroutinefunction(by_name["task_note"].fn))


class TestAtomicToolRouting(unittest.TestCase):
    def test_get_uid_tool_delegates_to_command(self):
        client = Mock()
        client.uid = 99
        command_service = make_service(client=client)
        tools = build_explicit_tools(command_service)
        get_uid_fn, _ = tools["get_uid"]
        self.assertEqual(get_uid_fn(), 99)

    def test_create_task_tool_forwards_arguments(self):
        client = Mock()
        client.__getitem__ = Mock(return_value=Mock(create=Mock(return_value=7)))
        command_service = make_service(client=client)
        tools = build_explicit_tools(command_service)
        create_fn, _ = tools["create_task"]
        result = create_fn("My Task", 3, "desc")
        self.assertEqual(result, 7)


if __name__ == "__main__":
    unittest.main()
