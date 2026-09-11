"""Client-observable native gating (#715).

The gate moved from a hand-rolled pre-filter to fastmcp's own visibility
machinery: :class:`OdooMCPServer` registers gated tools with the
``GATED_TOOL_TAG`` tag and disables that tag (``FastMCP.disable(tags=...)``)
unless ``ODOO_MCP_INCLUDE_GATED`` opts in. These tests assert the contract at
the only level that matters — what an MCP *client* observes over an in-memory
connection: a tag-disabled tool is absent from ``list_tools`` AND uncallable,
and the opt-in restores the full surface. The frozen name sets themselves are
guarded by ``test_default_surface_snapshot.py``; this file covers the
mechanism.
"""

import asyncio
import os
import unittest
from unittest.mock import Mock, patch

from fastmcp import Client
from fastmcp.exceptions import ToolError

from odoo_sdk.commands import Registry
from odoo_sdk.mcp.server import OdooMCPServer
from odoo_sdk.mcp.tools import (
    GATED_TOOL_NAMES,
    GATED_TOOLS_ENV,
    build_explicit_tools,
)

#: A real gated name and a real default name (sanity-checked in setUp) used as
#: minimal probes, so the callable/uncallable checks need no live registry.
_GATED_PROBE = "search_count"
_DEFAULT_PROBE = "get_uid"


def _probe_surface():
    """Two-tool surface: one gated name, one default name, trivial bodies."""

    def search_count() -> str:
        """Gated probe."""
        return "gated-result"

    def get_uid() -> str:
        """Default probe."""
        return "default-result"

    return {
        _GATED_PROBE: (search_count, "Gated probe."),
        _DEFAULT_PROBE: (get_uid, "Default probe."),
    }


def _server(tools):
    return OdooMCPServer(Registry(Mock()), explicit_tools=tools, serve_skills=False)


def _list_tool_names(server):
    async def go():
        async with Client(server.mcp) as client:
            return {tool.name for tool in await client.list_tools()}

    return asyncio.run(go())


def _call_tool(server, name):
    async def go():
        async with Client(server.mcp) as client:
            return await client.call_tool(name, {})

    return asyncio.run(go())


class _GatingCase(unittest.TestCase):
    def setUp(self):
        # The probe names must stay real members of their sets, or these tests
        # would silently exercise nothing.
        self.assertIn(_GATED_PROBE, GATED_TOOL_NAMES)
        self.assertNotIn(_DEFAULT_PROBE, GATED_TOOL_NAMES)


class TestDefaultSurfaceIsNativelyGated(_GatingCase):
    """Without the opt-in, a gated tool is invisible and uncallable."""

    def _probe_server(self):
        with patch.dict(os.environ, {GATED_TOOLS_ENV: ""}):
            return _server(_probe_surface())

    def test_gated_tool_absent_from_list_tools(self):
        self.assertEqual(_list_tool_names(self._probe_server()), {_DEFAULT_PROBE})

    def test_gated_tool_is_uncallable(self):
        server = self._probe_server()
        with self.assertRaises(ToolError):
            _call_tool(server, _GATED_PROBE)

    def test_ungated_tool_still_dispatches(self):
        result = _call_tool(self._probe_server(), _DEFAULT_PROBE)
        self.assertIn("default-result", str(result.content))


class TestOptInRestoresFullSurface(_GatingCase):
    """``ODOO_MCP_INCLUDE_GATED=1`` re-enables the gated tools wholesale."""

    def _probe_server(self):
        with patch.dict(os.environ, {GATED_TOOLS_ENV: "1"}):
            return _server(_probe_surface())

    def test_gated_tool_listed(self):
        self.assertEqual(
            _list_tool_names(self._probe_server()),
            {_DEFAULT_PROBE, _GATED_PROBE},
        )

    def test_gated_tool_dispatches(self):
        result = _call_tool(self._probe_server(), _GATED_PROBE)
        self.assertIn("gated-result", str(result.content))


class TestFullBuiltSurfaceParity(_GatingCase):
    """Handing the server the *full* built surface yields the frozen split.

    This is the migration's parity contract: registering all tools and
    disabling by tag is client-observably identical to the old pre-filter —
    the default exposure is exactly the built surface minus the gated names,
    and the opt-in exposes everything.
    """

    def test_default_exposure_is_full_minus_gated(self):
        full = build_explicit_tools(Registry(Mock()))
        with patch.dict(os.environ, {GATED_TOOLS_ENV: ""}):
            server = _server(full)
        self.assertEqual(_list_tool_names(server), set(full) - GATED_TOOL_NAMES)

    def test_opt_in_exposure_is_the_full_surface(self):
        full = build_explicit_tools(Registry(Mock()))
        with patch.dict(os.environ, {GATED_TOOLS_ENV: "1"}):
            server = _server(full)
        self.assertEqual(_list_tool_names(server), set(full))


if __name__ == "__main__":
    unittest.main()
