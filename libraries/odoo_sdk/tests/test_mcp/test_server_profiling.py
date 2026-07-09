"""Tests for opt-in per-call cProfile profiling in the MCP server."""

import asyncio
import cProfile
import inspect
import pstats
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, Mock, patch

from odoo_sdk.commands.command_registry import Registry
from odoo_sdk.mcp.server import OdooMCPServer, _dump_profile, _profiled


def _zips(directory: str) -> list[Path]:
    return sorted(Path(directory).glob("odoo_profile_*.zip"))


class TestProfiledWrapper(unittest.TestCase):
    def test_sync_tool_returns_value_and_writes_zip(self):
        def add(a: int, b: int) -> int:
            """Add two numbers."""
            return a + b

        with TemporaryDirectory() as tmp, patch(
            "tempfile.gettempdir", return_value=tmp
        ):
            result = _profiled(add, "add")(2, 3)
            zips = _zips(tmp)
        self.assertEqual(result, 5)
        self.assertEqual(len(zips), 1)
        self.assertTrue(zips[0].name.startswith("odoo_profile_add_"))

    def test_async_tool_is_awaited_and_writes_zip(self):
        async def fetch(value: int) -> int:
            """Double a value."""
            return value * 2

        with TemporaryDirectory() as tmp, patch(
            "tempfile.gettempdir", return_value=tmp
        ):
            result = asyncio.run(_profiled(fetch, "fetch")(21))
            zips = _zips(tmp)
        self.assertEqual(result, 42)
        self.assertEqual(len(zips), 1)

    def test_profile_is_dumped_even_when_tool_raises(self):
        def boom() -> None:
            """Always raise."""
            raise ValueError("nope")

        with TemporaryDirectory() as tmp, patch(
            "tempfile.gettempdir", return_value=tmp
        ):
            with self.assertRaises(ValueError):
                _profiled(boom, "boom")()
            zips = _zips(tmp)
        self.assertEqual(len(zips), 1)

    def test_repeated_calls_produce_distinct_archives(self):
        def noop() -> None:
            """Do nothing."""
            return None

        with TemporaryDirectory() as tmp, patch(
            "tempfile.gettempdir", return_value=tmp
        ):
            wrapped = _profiled(noop, "noop")
            wrapped()
            wrapped()
            names = {z.name for z in _zips(tmp)}
        self.assertEqual(len(names), 2)

    def test_signature_is_preserved(self):
        def echo(message: str, shout: bool = False) -> str:
            """Echo a message."""
            return message

        wrapped = _profiled(echo, "echo")
        self.assertEqual(inspect.signature(wrapped), inspect.signature(echo))


class TestDumpProfile(unittest.TestCase):
    def _profiler(self) -> cProfile.Profile:
        profiler = cProfile.Profile()
        profiler.enable()
        sum(range(100))
        profiler.disable()
        return profiler

    def test_zip_contains_single_loadable_prof(self):
        with TemporaryDirectory() as tmp, patch(
            "tempfile.gettempdir", return_value=tmp
        ):
            path = _dump_profile(self._profiler(), "calc")
            self.assertTrue(Path(path).is_absolute())
            with zipfile.ZipFile(path) as archive:
                self.assertEqual(archive.namelist(), ["calc.prof"])
                extracted = Path(tmp) / "extracted.prof"
                extracted.write_bytes(archive.read("calc.prof"))
            pstats.Stats(str(extracted))  # loads without error

    def test_no_intermediate_prof_left_behind(self):
        with TemporaryDirectory() as tmp, patch(
            "tempfile.gettempdir", return_value=tmp
        ):
            _dump_profile(self._profiler(), "calc")
            leftover = list(Path(tmp).glob("*.prof"))
        self.assertEqual(leftover, [])


def _registry() -> Registry:
    return Registry(Mock())


class TestServerProfilingWiring(unittest.TestCase):
    def test_profiling_defaults_off_and_is_stored(self):
        with patch("odoo_sdk.mcp.server.FastMCP"):
            off = OdooMCPServer(_registry())
            on = OdooMCPServer(_registry(), profiling=True)
        self.assertFalse(off.profiling)
        self.assertTrue(on.profiling)

    def test_tools_not_wrapped_when_profiling_off(self):
        def a() -> str:
            """Tool A."""
            return "a"

        with patch(
            "odoo_sdk.mcp.server.FastMCP", return_value=MagicMock()
        ), patch("odoo_sdk.mcp.server._profiled") as mock_profiled:
            OdooMCPServer(_registry(), explicit_tools={"a": a}, profiling=False)
        mock_profiled.assert_not_called()

    def test_tools_wrapped_when_profiling_on(self):
        def a() -> str:
            """Tool A."""
            return "a"

        with patch(
            "odoo_sdk.mcp.server.FastMCP", return_value=MagicMock()
        ), patch("odoo_sdk.mcp.server._profiled", return_value=a) as mock_profiled:
            OdooMCPServer(_registry(), explicit_tools={"a": a}, profiling=True)
        mock_profiled.assert_called_once_with(a, "a")


if __name__ == "__main__":
    unittest.main()
