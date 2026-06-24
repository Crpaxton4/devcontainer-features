"""Tests for the async _build_tool branch in OdooMCPServer."""

import asyncio
import unittest
from unittest.mock import MagicMock, Mock, patch

from odoo_sdk.commands.command import Command
from odoo_sdk.commands.command_registry import Registry
from odoo_sdk.mcp.server import OdooMCPServer


class AsyncCommand(Command):
    """Command with an async execute method."""

    _name = "async_cmd"
    _description = "An async command."

    async def execute(self, value: int) -> int:
        return value * 2


class SyncCommand(Command):
    """Command with a sync execute method."""

    _name = "sync_cmd"
    _description = "A sync command."

    def execute(self, value: int) -> int:
        return value + 1


def _build_server(*commands):
    registry = Registry(Mock())
    for cmd in commands:
        registry.register(cmd._name, cmd)
    mock_mcp = MagicMock()
    added = []
    mock_mcp.add_tool.side_effect = added.append
    with patch("odoo_sdk.mcp.server.FastMCP", return_value=mock_mcp):
        server = OdooMCPServer(registry)
    return server, added


class TestAsyncToolWrap(unittest.TestCase):
    def test_async_command_produces_coroutine_function(self):
        _, added = _build_server(AsyncCommand)
        import asyncio
        self.assertTrue(asyncio.iscoroutinefunction(added[0].fn))

    def test_sync_command_produces_regular_function(self):
        _, added = _build_server(SyncCommand)
        self.assertFalse(asyncio.iscoroutinefunction(added[0].fn))

    def test_async_tool_returns_correct_result(self):
        _, added = _build_server(AsyncCommand)
        result = asyncio.run(added[0].fn(value=5))
        self.assertEqual(result, 10)

    def test_sync_tool_returns_correct_result(self):
        _, added = _build_server(SyncCommand)
        result = added[0].fn(value=3)
        self.assertEqual(result, 4)

    def test_both_tools_registered_independently(self):
        _, added = _build_server(AsyncCommand, SyncCommand)
        self.assertEqual(len(added), 2)
        names = {t.name for t in added}
        self.assertEqual(names, {"async_cmd", "sync_cmd"})


if __name__ == "__main__":
    unittest.main()
