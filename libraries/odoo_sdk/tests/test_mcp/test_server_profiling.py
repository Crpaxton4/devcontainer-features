"""Tests for opt-in per-call cProfile profiling in the MCP server."""

import asyncio
import cProfile
import inspect
import os
import pstats
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, Mock, patch

from odoo_sdk.commands.command_registry import Registry
from odoo_sdk.mcp.server import (
    PROFILE_KEEP_LAST,
    PROFILE_SUBDIR,
    OdooMCPServer,
    _dump_profile,
    _profiled,
    _prune_profiles,
)


def _zips(directory: str) -> list[Path]:
    return sorted((Path(directory) / PROFILE_SUBDIR).glob("odoo_profile_*.zip"))


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
            leftover = list((Path(tmp) / PROFILE_SUBDIR).glob("*.prof"))
        self.assertEqual(leftover, [])

    def test_archive_is_written_into_dedicated_subdir(self):
        with TemporaryDirectory() as tmp, patch(
            "tempfile.gettempdir", return_value=tmp
        ):
            path = _dump_profile(self._profiler(), "calc")
            subdir = Path(tmp) / PROFILE_SUBDIR
            self.assertTrue(subdir.is_dir())
            self.assertEqual(Path(path).parent, subdir.resolve())

    def test_dumps_are_bounded_to_keep_last(self):
        with TemporaryDirectory() as tmp, patch(
            "tempfile.gettempdir", return_value=tmp
        ):
            for _ in range(PROFILE_KEEP_LAST + 3):
                _dump_profile(self._profiler(), "calc")
            zips = _zips(tmp)
        self.assertEqual(len(zips), PROFILE_KEEP_LAST)


class TestPruneProfiles(unittest.TestCase):
    def _seed(self, directory: Path, count: int) -> list[str]:
        """Create ``count`` archives with strictly increasing mtimes.

        :return: Archive filenames ordered oldest-first (index 0 is oldest).
        """
        names = []
        for index in range(count):
            path = directory / f"odoo_profile_tool_{index:03d}.zip"
            path.write_bytes(b"stub")
            stamp = 1_000_000_000 + index
            os.utime(path, ns=(stamp, stamp))
            names.append(path.name)
        return names

    def test_keep_last_constant_is_twenty(self):
        self.assertEqual(PROFILE_KEEP_LAST, 20)

    def test_prunes_oldest_and_keeps_newest_by_mtime(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            total = PROFILE_KEEP_LAST + 5
            names = self._seed(directory, total)
            _prune_profiles(directory)
            remaining = sorted(p.name for p in directory.glob("*.zip"))
        expected = sorted(names[total - PROFILE_KEEP_LAST :])
        self.assertEqual(len(remaining), PROFILE_KEEP_LAST)
        self.assertEqual(remaining, expected)
        self.assertNotIn(names[0], remaining)

    def test_keeps_all_when_at_or_below_limit(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            names = self._seed(directory, PROFILE_KEEP_LAST)
            _prune_profiles(directory)
            remaining = sorted(p.name for p in directory.glob("*.zip"))
        self.assertEqual(remaining, sorted(names))

    def test_prunes_only_matching_archives(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self._seed(directory, PROFILE_KEEP_LAST + 2)
            unrelated = directory / "keep-me.txt"
            unrelated.write_text("not a profile")
            _prune_profiles(directory)
            self.assertTrue(unrelated.exists())
            zips = list(directory.glob("odoo_profile_*.zip"))
        self.assertEqual(len(zips), PROFILE_KEEP_LAST)


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
        # TOON wraps first (inner), profiling wraps the result (outer), so the
        # tool passed to _profiled is the TOON wrapper around ``a``, not ``a``.
        mock_profiled.assert_called_once()
        wrapped_fn, tool_name = mock_profiled.call_args.args
        self.assertEqual(tool_name, "a")
        self.assertIs(wrapped_fn.__wrapped__, a)


if __name__ == "__main__":
    unittest.main()
