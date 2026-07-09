import asyncio
import cProfile
import functools
import inspect
import logging
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any, Callable, Optional, Tuple, Union

from fastmcp import FastMCP
from fastmcp.tools.tool import Tool

from odoo_sdk.commands import Registry

log = logging.getLogger(__name__)

# A tool spec is either a bare callable, or a ``(callable, description)`` pair.
ToolSpec = Union[Callable[..., Any], Tuple[Callable[..., Any], str]]


class OdooMCPServer:
    """Expose a set of explicit MCP tools built from a command :class:`Registry`.

    Every tool is defined explicitly in :mod:`odoo_sdk.mcp.tools` — one function
    per tool with a real, typed signature that delegates to a command. The server
    performs no auto-reflection of command ``execute`` signatures: the tool
    surface is exactly what ``explicit_tools`` provides. This keeps the wire
    schema an intentional part of the interaction surface and lets composition
    tools (which take the FastMCP ``ctx``) coexist with atomic tools uniformly.

    :param registry: Registry that owns the shared command dependencies. It is
        retained for prompt registration and is the registry the explicit tools
        compose.
    :type registry: Registry
    :param server_name: Human-readable name advertised by the MCP server.
    :type server_name: str
    :param explicit_tools: Mapping of tool name to either a tool callable or a
        ``(callable, description)`` pair. When omitted, the server exposes no
        tools (only prompts), defaults to None.
    :type explicit_tools: Optional[dict[str, ToolSpec]]
    :param profiling: When True, wrap every tool dispatch in :mod:`cProfile` and
        dump a zipped profile per call. Defaults to False (zero overhead).
    :type profiling: bool
    """

    def __init__(
        self,
        registry: Registry,
        server_name: str = "Odoo MCP Server",
        explicit_tools: Optional[dict[str, ToolSpec]] = None,
        profiling: bool = False,
    ):
        from odoo_sdk.mcp.prompts import register_builtin_prompts

        self.registry: Registry = registry
        self._explicit_tools: dict[str, ToolSpec] = explicit_tools or {}
        self.profiling: bool = profiling
        self.mcp: FastMCP = FastMCP(
            server_name,
            instructions="Provides tools for interacting with Odoo ERP",
        )
        self._register_tools()
        register_builtin_prompts(self.mcp, self.registry)

    def _register_tools(self) -> None:
        """Register each explicit tool with the FastMCP server.

        When profiling is enabled the tool callable is wrapped so every dispatch
        is profiled; the wrapper preserves the original typed signature so the
        wire schema (including any FastMCP ``ctx`` parameter) is unchanged.

        :return: None.
        :rtype: None
        """

        for name, spec in self._explicit_tools.items():
            tool_fn, description = self._unpack_spec(spec)
            if self.profiling:
                tool_fn = _profiled(tool_fn, name)
            self.mcp.add_tool(
                Tool.from_function(tool_fn, name=name, description=description or None)
            )

    @staticmethod
    def _unpack_spec(spec: ToolSpec) -> Tuple[Callable[..., Any], str]:
        """Normalize a tool spec into a ``(callable, description)`` pair.

        :param spec: Bare callable or ``(callable, description)`` pair.
        :type spec: ToolSpec
        :return: The tool callable and its description (``""`` when absent).
        :rtype: Tuple[Callable[..., Any], str]
        """

        if isinstance(spec, tuple):
            tool_fn, description = spec
            return tool_fn, description
        return spec, ""

    def run(self, **kwargs: Any) -> None:
        """Start the FastMCP server.

        :param kwargs: Transport options forwarded to ``FastMCP.run`` (defaults
            to stdio when none are given).
        :return: None.
        :rtype: None
        """

        self.mcp.run(**kwargs)


def _profiled(tool_fn: Callable[..., Any], tool_name: str) -> Callable[..., Any]:
    """Wrap a tool callable so each dispatch is captured with :mod:`cProfile`.

    Sync and async tools are handled separately so the wrapper awaits coroutine
    tools correctly. The profile is dumped in a ``finally`` block so it is still
    written when the tool raises, and the original typed signature is preserved
    so FastMCP builds the same wire schema (including any ``ctx`` parameter).

    :param tool_fn: The tool callable to wrap.
    :type tool_fn: Callable[..., Any]
    :param tool_name: Public tool name used in the profile and archive names.
    :type tool_name: str
    :return: A signature-preserving wrapper that profiles each dispatch.
    :rtype: Callable[..., Any]
    """
    if asyncio.iscoroutinefunction(tool_fn):

        @functools.wraps(tool_fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            profiler = cProfile.Profile()
            profiler.enable()
            try:
                return await tool_fn(*args, **kwargs)
            finally:
                profiler.disable()
                _dump_profile(profiler, tool_name)

    else:

        @functools.wraps(tool_fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            profiler = cProfile.Profile()
            profiler.enable()
            try:
                return tool_fn(*args, **kwargs)
            finally:
                profiler.disable()
                _dump_profile(profiler, tool_name)

    wrapper.__signature__ = inspect.signature(tool_fn)
    return wrapper


def _dump_profile(profiler: cProfile.Profile, tool_name: str) -> str:
    """Write a profiler snapshot as a zipped ``.prof`` file to the temp dir.

    This helper keeps the tool wrapper thin: it persists the captured stats to
    disk, compresses the binary profile into a shareable zip, and logs the
    absolute path so tooling can locate the artifact.

    :param profiler: Disabled profiler holding the captured call stats.
    :type profiler: cProfile.Profile
    :param tool_name: Public tool name used in the profile and archive names.
    :type tool_name: str
    :return: Absolute path to the written zip archive.
    :rtype: str
    """
    tempdir = Path(tempfile.gettempdir())
    # Nanosecond suffix keeps each call's archive unique even when the same tool
    # is dispatched multiple times within the same wall-clock second.
    timestamp = f"{time.strftime('%Y%m%d_%H%M%S')}_{time.time_ns() % 1_000_000_000}"
    prof_path = tempdir / f"{tool_name}_{timestamp}.prof"
    zip_path = tempdir / f"odoo_profile_{tool_name}_{timestamp}.zip"

    profiler.dump_stats(str(prof_path))
    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(prof_path, arcname=f"{tool_name}.prof")
    finally:
        prof_path.unlink(missing_ok=True)

    absolute = str(zip_path.resolve())
    log.info("Profile saved: %s", absolute)
    return absolute
