"""Integration tests: build_explicit_tools wired into OdooMCPServer.

Verifies the full builtin tool surface is registered explicitly (no
auto-reflection) and that composition tools remain async.
"""

import asyncio
import unittest
from unittest.mock import MagicMock, Mock, patch

from odoo_sdk.commands import Registry
from odoo_sdk.commands.builtin import BUILTIN_COMMANDS, register_builtins
from odoo_sdk.mcp.server import OdooMCPServer
from odoo_sdk.mcp.tools import build_explicit_tools


#: Built-ins deliberately absent from the MCP surface. Surface selection is the
#: consumer's concern, never the producer's (#499): the command registry knows
#: nothing about the layers above it, and MCP names its tools explicitly in
#: ``TOOL_FACTORIES``, so registering a builtin does not expose it as a tool.
#: ``get_employee_id`` is only needed by the unattended timesheet-export path,
#: which runs with no LLM in the loop.
NON_MCP_BUILTINS = {"get_employee_id"}


def _mcp_tool_names() -> set:
    """Return the builtin names that are expected on the MCP tool surface."""
    return set(BUILTIN_COMMANDS) - NON_MCP_BUILTINS


def _full_registry() -> Registry:
    return register_builtins(Registry(Mock()))


def _build(registry, explicit_tools):
    mock_mcp = MagicMock()
    added = []
    mock_mcp.add_tool.side_effect = added.append
    with patch("odoo_sdk.mcp.server.FastMCP", return_value=mock_mcp):
        OdooMCPServer(registry, explicit_tools=explicit_tools)
    return added


class TestFullToolSurface(unittest.TestCase):
    def test_every_builtin_command_has_an_explicit_tool(self):
        registry = _full_registry()
        tools = build_explicit_tools(registry)
        self.assertEqual(set(tools), _mcp_tool_names())

    def test_server_registers_all_builtin_tools(self):
        registry = _full_registry()
        added = _build(registry, build_explicit_tools(registry))
        self.assertEqual({t.name for t in added}, _mcp_tool_names())

    def test_descriptions_sourced_from_commands(self):
        registry = _full_registry()
        added = _build(registry, build_explicit_tools(registry))
        by_name = {t.name for t in added if t.description}
        # Every tool has a non-empty description.
        self.assertEqual(by_name, _mcp_tool_names())

    def test_non_mcp_builtins_are_registered_but_not_exposed(self):
        registry = _full_registry()
        tools = build_explicit_tools(registry)
        for name in NON_MCP_BUILTINS:
            self.assertIn(name, BUILTIN_COMMANDS)
            self.assertNotIn(name, tools)

    def test_composition_tools_are_async(self):
        registry = _full_registry()
        added = _build(registry, build_explicit_tools(registry))
        by_name = {t.name: t for t in added}
        self.assertTrue(asyncio.iscoroutinefunction(by_name["start_task"].fn))
        self.assertTrue(asyncio.iscoroutinefunction(by_name["stop_task"].fn))

    def test_atomic_tools_are_sync(self):
        registry = _full_registry()
        added = _build(registry, build_explicit_tools(registry))
        by_name = {t.name: t for t in added}
        self.assertFalse(asyncio.iscoroutinefunction(by_name["get_uid"].fn))
        self.assertFalse(asyncio.iscoroutinefunction(by_name["task_note"].fn))


class TestAtomicToolRouting(unittest.TestCase):
    def test_get_uid_tool_delegates_to_command(self):
        client = Mock()
        client.uid = 99
        registry = register_builtins(Registry(client))
        tools = build_explicit_tools(registry)
        get_uid_fn, _ = tools["get_uid"]
        self.assertEqual(get_uid_fn(), 99)

    def test_create_task_tool_forwards_arguments(self):
        client = Mock()
        client.__getitem__ = Mock(return_value=Mock(create=Mock(return_value=7)))
        registry = register_builtins(Registry(client))
        tools = build_explicit_tools(registry)
        create_fn, _ = tools["create_task"]
        result = create_fn("My Task", 3, "desc")
        self.assertEqual(result, 7)


if __name__ == "__main__":
    unittest.main()
