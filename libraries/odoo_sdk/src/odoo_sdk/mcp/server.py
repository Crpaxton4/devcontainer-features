import asyncio
import cProfile
import functools
import inspect
import logging
import os
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

#: Environment variable that gates TOON-encoded tool output. When set to a
#: truthy value (``1``/``true``/``yes``/``on``), structured tool results are
#: serialized to `TOON <https://github.com/toon-format/toon-python>`_ instead of
#: being returned as native objects, trading a marginal encoding cost for a
#: substantial reduction in the tokens an LLM spends reading the result.
TOON_OUTPUT_ENV = "ODOO_TOON_OUTPUT"

#: Name of the subdirectory (under the system temp dir) that holds MCP profiling
#: archives. Confining artifacts to a dedicated folder keeps pruning scoped to
#: the SDK's own files and away from anything else living in the temp dir.
PROFILE_SUBDIR = "odoo-sdk-profiles"

#: Maximum number of profiling zips retained on disk. After every dump the
#: oldest archives beyond this count are deleted, so an enabled profiler cannot
#: grow the temp dir without bound over a container's lifetime.
PROFILE_KEEP_LAST = 20

#: Default server identity advertised to MCP clients. The server class is
#: otherwise registry-generic, so this is the one Odoo-specific string; a
#: non-Odoo registry passes its own text via the ``instructions`` parameter.
DEFAULT_INSTRUCTIONS = "Provides tools for interacting with Odoo ERP"


def _toon_output_enabled() -> bool:
    """Return whether TOON output is enabled via the environment flag.

    Reading the flag on every call (rather than at import time) keeps the
    behavior togglable per process invocation and makes the gate trivial to
    exercise from tests.

    :return: ``True`` when :data:`TOON_OUTPUT_ENV` is set to a truthy value.
    :rtype: bool
    """

    return os.environ.get(TOON_OUTPUT_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _to_toon(result: Any) -> Any:
    """Serialize a structured tool ``result`` to TOON, gated by the env flag.

    Only dicts and lists are converted; scalars and any values TOON cannot
    encode are returned unchanged so existing behavior is preserved when the
    flag is off or the payload is not structured data.

    :param result: The raw value returned by a command's ``execute``.
    :type result: Any
    :return: A TOON string when conversion applies, otherwise ``result``.
    :rtype: Any
    """

    if not _toon_output_enabled() or not isinstance(result, (dict, list)):
        return result
    from toon_format import encode

    try:
        return encode(result)
    except Exception:
        # Defensive: never let an encoding hiccup break a tool call; fall back
        # to the raw payload so the flag-on path degrades to flag-off behavior.
        return result


def _toon_encoded(tool_fn: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a tool callable so its result is routed through :func:`_to_toon`.

    The wrapper is always applied, but :func:`_to_toon` is a no-op unless the
    ``ODOO_TOON_OUTPUT`` flag is set, so behavior is unchanged when the flag is
    off. Sync and async tools are handled separately, and the original typed
    signature is preserved so FastMCP builds the same wire schema (including any
    ``ctx`` parameter).

    :param tool_fn: The tool callable to wrap.
    :type tool_fn: Callable[..., Any]
    :return: A signature-preserving wrapper that TOON-encodes the result.
    :rtype: Callable[..., Any]
    """
    if asyncio.iscoroutinefunction(tool_fn):

        @functools.wraps(tool_fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            return _to_toon(await tool_fn(*args, **kwargs))

    else:

        @functools.wraps(tool_fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return _to_toon(tool_fn(*args, **kwargs))

    wrapper.__signature__ = inspect.signature(tool_fn)
    return wrapper


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
    :param instructions: Server identity advertised to MCP clients. Defaults to
        :data:`DEFAULT_INSTRUCTIONS`; a non-Odoo registry passes its own text.
    :type instructions: str
    """

    def __init__(
        self,
        registry: Registry,
        server_name: str = "Odoo MCP Server",
        explicit_tools: Optional[dict[str, ToolSpec]] = None,
        profiling: bool = False,
        instructions: str = DEFAULT_INSTRUCTIONS,
    ):
        from odoo_sdk.mcp.prompts import register_builtin_prompts

        self.registry: Registry = registry
        self._explicit_tools: dict[str, ToolSpec] = explicit_tools or {}
        self.profiling: bool = profiling
        self.mcp: FastMCP = FastMCP(
            server_name,
            instructions=instructions,
        )
        self._register_tools()
        register_builtin_prompts(self.mcp, self.registry)

    def _register_tools(self) -> None:
        """Register each explicit tool with the FastMCP server.

        Each tool callable is wrapped so its result is routed through
        :func:`_to_toon` (a no-op unless ``ODOO_TOON_OUTPUT`` is set) and, when
        profiling is enabled, wrapped again so every dispatch is profiled. Both
        wrappers preserve the original typed signature so the wire schema
        (including any FastMCP ``ctx`` parameter) is unchanged.

        :return: None.
        :rtype: None
        """

        for name, spec in self._explicit_tools.items():
            tool_fn, description = self._unpack_spec(spec)
            tool_fn = _toon_encoded(tool_fn)
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


def _profile_dir() -> Path:
    """Return the profiling-archive directory, creating it if absent.

    Archives live in a dedicated :data:`PROFILE_SUBDIR` under the system temp
    dir so pruning only ever touches the SDK's own files. The system temp dir is
    resolved on every call (rather than cached) so tests can redirect it.

    :return: The existing archive directory.
    :rtype: Path
    """

    directory = Path(tempfile.gettempdir()) / PROFILE_SUBDIR
    directory.mkdir(exist_ok=True)
    return directory


def _prune_profiles(directory: Path) -> None:
    """Delete the oldest archives, retaining at most :data:`PROFILE_KEEP_LAST`.

    Archives are ordered oldest-first by modification time (with the filename as
    a stable tiebreaker), and everything before the newest
    :data:`PROFILE_KEEP_LAST` entries is removed. When the directory holds at
    most that many archives nothing is deleted.

    :param directory: Directory whose ``odoo_profile_*.zip`` archives to prune.
    :type directory: Path
    :return: None.
    :rtype: None
    """

    archives = sorted(
        directory.glob("odoo_profile_*.zip"),
        key=lambda path: (path.stat().st_mtime_ns, path.name),
    )
    for stale in archives[:-PROFILE_KEEP_LAST]:
        stale.unlink(missing_ok=True)


def _dump_profile(profiler: cProfile.Profile, tool_name: str) -> str:
    """Write a profiler snapshot as a zipped ``.prof`` file, then prune old ones.

    This helper keeps the tool wrapper thin: it persists the captured stats to
    disk, compresses the binary profile into a shareable zip under
    :data:`PROFILE_SUBDIR`, prunes the archive directory back to
    :data:`PROFILE_KEEP_LAST` files, and logs the absolute path so tooling can
    locate the artifact.

    :param profiler: Disabled profiler holding the captured call stats.
    :type profiler: cProfile.Profile
    :param tool_name: Public tool name used in the profile and archive names.
    :type tool_name: str
    :return: Absolute path to the written zip archive.
    :rtype: str
    """
    directory = _profile_dir()
    # Zero-padded nanoseconds keep each call's archive unique, and make same-tool
    # filenames sort in creation order even within one wall-clock second.
    timestamp = (
        f"{time.strftime('%Y%m%d_%H%M%S')}_{time.time_ns() % 1_000_000_000:09d}"
    )
    prof_path = directory / f"{tool_name}_{timestamp}.prof"
    zip_path = directory / f"odoo_profile_{tool_name}_{timestamp}.zip"

    profiler.dump_stats(str(prof_path))
    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(prof_path, arcname=f"{tool_name}.prof")
    finally:
        prof_path.unlink(missing_ok=True)

    _prune_profiles(directory)
    absolute = str(zip_path.resolve())
    log.info("Profile saved: %s", absolute)
    return absolute
