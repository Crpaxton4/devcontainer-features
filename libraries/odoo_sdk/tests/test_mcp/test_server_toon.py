"""Tests for the TOON-encoded tool output path in OdooMCPServer.

The behavior is gated behind the ``ODOO_TOON_OUTPUT`` environment flag and must
stay off by default, so these tests toggle the flag explicitly around each case.
"""

import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, Mock, patch

from odoo_sdk.commands.command_registry import Registry
from odoo_sdk.mcp import server
from odoo_sdk.mcp.server import OdooMCPServer, _to_toon, _toon_output_enabled


def _dict_tool() -> dict:
    """Return a structured dict."""
    return {"ok": True, "value": 42}


def _list_tool() -> list:
    """Return a list of records."""
    return [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]


def _scalar_tool() -> str:
    """Return a plain scalar."""
    return "plain-string"


async def _async_dict_tool() -> dict:
    """Return a dict asynchronously."""
    return {"async": True}


def _build_added(name, tool_fn):
    """Register one explicit tool and return the resulting FastMCP ``Tool``."""
    registry = Registry(Mock())
    mock_mcp = MagicMock()
    added = []
    mock_mcp.add_tool.side_effect = added.append
    with patch("odoo_sdk.mcp.server.FastMCP", return_value=mock_mcp):
        OdooMCPServer(registry, explicit_tools={name: tool_fn})
    return added[0]


class TestToonOutputFlag(unittest.TestCase):
    def test_flag_off_by_default(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(_toon_output_enabled())

    def test_flag_truthy_values(self):
        for value in ("1", "true", "TRUE", "Yes", "on", " on "):
            with self.subTest(value=value):
                with patch.dict("os.environ", {server.TOON_OUTPUT_ENV: value}):
                    self.assertTrue(_toon_output_enabled())

    def test_flag_falsy_values(self):
        for value in ("0", "false", "no", "off", ""):
            with self.subTest(value=value):
                with patch.dict("os.environ", {server.TOON_OUTPUT_ENV: value}):
                    self.assertFalse(_toon_output_enabled())


class TestToToon(unittest.TestCase):
    def test_dict_encoded_when_flag_on(self):
        with patch.dict("os.environ", {server.TOON_OUTPUT_ENV: "1"}):
            out = _to_toon({"ok": True, "value": 42})
        self.assertIsInstance(out, str)
        self.assertIn("ok: true", out)
        self.assertIn("value: 42", out)

    def test_list_encoded_when_flag_on(self):
        with patch.dict("os.environ", {server.TOON_OUTPUT_ENV: "1"}):
            out = _to_toon([{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}])
        self.assertIsInstance(out, str)
        self.assertIn("Alice", out)
        self.assertIn("Bob", out)

    def test_dict_untouched_when_flag_off(self):
        payload = {"ok": True}
        with patch.dict("os.environ", {}, clear=True):
            self.assertIs(_to_toon(payload), payload)

    def test_scalar_untouched_when_flag_on(self):
        with patch.dict("os.environ", {server.TOON_OUTPUT_ENV: "1"}):
            self.assertEqual(_to_toon("plain"), "plain")
            self.assertEqual(_to_toon(7), 7)

    def test_encode_failure_falls_back_to_raw(self):
        payload = {"a": 1}
        with (
            patch.dict("os.environ", {server.TOON_OUTPUT_ENV: "1"}),
            patch("toon_format.encode", side_effect=ValueError("boom")),
        ):
            self.assertIs(_to_toon(payload), payload)


class TestToolWrapperUsesToon(unittest.TestCase):
    def test_sync_dict_tool_returns_toon_when_flag_on(self):
        tool = _build_added("dict_tool", _dict_tool)
        with patch.dict("os.environ", {server.TOON_OUTPUT_ENV: "1"}):
            result = tool.fn()
        self.assertIsInstance(result, str)
        self.assertIn("ok: true", result)

    def test_sync_list_tool_returns_toon_when_flag_on(self):
        tool = _build_added("list_tool", _list_tool)
        with patch.dict("os.environ", {server.TOON_OUTPUT_ENV: "1"}):
            result = tool.fn()
        self.assertIsInstance(result, str)
        self.assertIn("Alice", result)

    def test_sync_tool_returns_raw_when_flag_off(self):
        tool = _build_added("dict_tool", _dict_tool)
        with patch.dict("os.environ", {}, clear=True):
            result = tool.fn()
        self.assertEqual(result, {"ok": True, "value": 42})

    def test_scalar_tool_unchanged_when_flag_on(self):
        tool = _build_added("scalar_tool", _scalar_tool)
        with patch.dict("os.environ", {server.TOON_OUTPUT_ENV: "1"}):
            self.assertEqual(tool.fn(), "plain-string")

    def test_async_dict_tool_returns_toon_when_flag_on(self):
        tool = _build_added("async_dict_tool", _async_dict_tool)
        with patch.dict("os.environ", {server.TOON_OUTPUT_ENV: "1"}):
            result = asyncio.run(tool.fn())
        self.assertIsInstance(result, str)
        self.assertIn("async: true", result)

    def test_async_dict_tool_returns_raw_when_flag_off(self):
        tool = _build_added("async_dict_tool", _async_dict_tool)
        with patch.dict("os.environ", {}, clear=True):
            result = asyncio.run(tool.fn())
        self.assertEqual(result, {"async": True})


class TestToonComposesWithProfiling(unittest.TestCase):
    """TOON output and cProfile profiling wrap the same tool and compose."""

    def test_both_wrappers_apply_when_enabled(self):
        registry = Registry(Mock())
        mock_mcp = MagicMock()
        added = []
        mock_mcp.add_tool.side_effect = added.append
        with TemporaryDirectory() as tmp, patch(
            "tempfile.gettempdir", return_value=tmp
        ), patch.dict("os.environ", {server.TOON_OUTPUT_ENV: "1"}), patch(
            "odoo_sdk.mcp.server.FastMCP", return_value=mock_mcp
        ):
            OdooMCPServer(
                registry, explicit_tools={"dict_tool": _dict_tool}, profiling=True
            )
            result = added[0].fn()
            profiles = sorted(
                (Path(tmp) / server.PROFILE_SUBDIR).glob("odoo_profile_*.zip")
            )
        self.assertIsInstance(result, str)  # TOON-encoded
        self.assertIn("ok: true", result)
        self.assertEqual(len(profiles), 1)  # profile written


if __name__ == "__main__":
    unittest.main()
