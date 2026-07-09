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
        self.assertEqual(set(tools), set(BUILTIN_COMMANDS))

    def test_server_registers_all_builtin_tools(self):
        registry = _full_registry()
        added = _build(registry, build_explicit_tools(registry))
        self.assertEqual({t.name for t in added}, set(BUILTIN_COMMANDS))

    def test_descriptions_sourced_from_commands(self):
        registry = _full_registry()
        added = _build(registry, build_explicit_tools(registry))
        by_name = {t.name for t in added if t.description}
        # Every builtin has a non-empty description.
        self.assertEqual(by_name, set(BUILTIN_COMMANDS))

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
