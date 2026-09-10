import asyncio
import contextlib
import cProfile
import functools
import inspect
import itertools
import logging
import os
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any, Callable, Optional, Tuple, Union

from fastmcp import FastMCP
from fastmcp.tools import Tool
from mcp.types import InputRequiredResult

from odoo_sdk.commands import Registry

# Re-imported under their historical module attributes (#713): the dispatch
# boundary vocabulary and telemetry emitter moved to the core layer
# (odoo_sdk.commands.dispatch_telemetry) so the CLI ``cmd`` dispatcher can share
# them without importing a surface, while every existing patch target on this
# module (``odoo_sdk.mcp.server._emit_tool_event`` and friends) keeps working.
from odoo_sdk.commands.dispatch_telemetry import (  # noqa: F401
    _BOUNDARY_ERRORS,
    _RESULT_PAYLOAD_KEYS,
    _emit_tool_event,
    _error_payload,
    _event_task_ids,
    _outcome_line,
    _result_payload_fields,
)

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

#: Zero-padding width for the per-process dump sequence embedded in profiling
#: filenames. Six digits covers a million dumps in one process before the
#: rendered counter outgrows the field; past that it simply widens (it never
#: wraps, so uniqueness never lapses) and only the lexicographic tiebreak
#: degrades -- and only between two dumps that also landed in the same
#: wall-clock second and the same nanosecond bucket, a million dumps apart.
PROFILE_SEQ_WIDTH = 6

#: Monotonic per-process dump counter, the component that actually makes each
#: profiling filename unique. ``next()`` on an :func:`itertools.count` is a
#: single atomic bytecode under the GIL, so the profiled wrapper can be entered
#: concurrently without a lock and still hand out strictly increasing values.
_profile_seq = itertools.count()

#: Default server identity advertised to MCP clients. The server class is
#: otherwise registry-generic, so this is the one Odoo-specific string; a
#: non-Odoo registry passes its own text via the ``instructions`` parameter.
DEFAULT_INSTRUCTIONS = "Provides tools for interacting with Odoo ERP"


def _toon_output_enabled() -> bool:
    """Return whether TOON output is enabled via the environment flag.

    Read per call (not at import) so the gate stays togglable per process and
    trivial to exercise from tests.
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


def _wrap_tool(
    tool_fn: Callable[..., Any],
    *,
    on_result: Callable[[Any], Any] = lambda result: result,
    catch: Tuple[type[BaseException], ...] = (),
    on_error: Optional[Callable[[BaseException], Any]] = None,
    on_success: Optional[Callable[[Any, tuple, dict], None]] = None,
    setup: Optional[Callable[[], Any]] = None,
    teardown: Optional[Callable[[Any], None]] = None,
) -> Callable[..., Any]:
    """Build a signature-preserving sync/async wrapper from a set of hooks.

    One combinator behind every MCP tool decorator: it owns the
    coroutine-vs-plain branch and the ``functools.wraps`` scaffold once, so each
    decorator supplies only the hooks it needs — ``on_result`` transforms a
    successful result, ``catch``/``on_error`` map a caught exception to a return
    value, ``on_success`` fires a side effect after a successful call (receiving
    the transformed result and the call's args/kwargs, so the event emitter can
    derive an outcome payload from it — #626), and ``setup``/``teardown``
    bracket every dispatch (teardown runs in ``finally``, so it still fires when
    the tool raises). ``functools.wraps`` sets
    ``__wrapped__``, which ``inspect.signature`` follows, so FastMCP builds the
    same wire schema (including any ``ctx`` parameter) without an explicit
    ``__signature__`` assignment.

    :param tool_fn: The tool callable to wrap.
    :type tool_fn: Callable[..., Any]
    :return: A signature-preserving wrapper applying the given hooks.
    :rtype: Callable[..., Any]
    """

    def _finish(result: Any, args: tuple, kwargs: dict) -> Any:
        result = on_result(result)
        if on_success is not None:
            on_success(result, args, kwargs)
        return result

    @contextlib.contextmanager
    def _bracket() -> Any:
        token = setup() if setup is not None else None
        try:
            yield
        finally:
            if teardown is not None:
                teardown(token)

    if asyncio.iscoroutinefunction(tool_fn):

        @functools.wraps(tool_fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            with _bracket():
                try:
                    return _finish(await tool_fn(*args, **kwargs), args, kwargs)
                except catch as exc:
                    return on_error(exc)  # type: ignore[misc]

    else:

        @functools.wraps(tool_fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with _bracket():
                try:
                    return _finish(tool_fn(*args, **kwargs), args, kwargs)
                except catch as exc:
                    return on_error(exc)  # type: ignore[misc]

    return wrapper


def _toon_encoded(tool_fn: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a tool callable so its result is routed through :func:`_to_toon`.

    The wrapper is always applied, but :func:`_to_toon` is a no-op unless the
    ``ODOO_TOON_OUTPUT`` flag is set, so behavior is unchanged when the flag is
    off.
    """
    return _wrap_tool(tool_fn, on_result=_to_toon)


def _error_boundary(tool_fn: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a tool callable so caller-actionable failures return a payload.

    Exceptions listed in :data:`_BOUNDARY_ERRORS` are converted into one
    predictable ``{"error": {"type", "message"}}`` shape so LLM callers always
    see structured data instead of a stack trace; programming errors propagate
    unchanged. Applied inside :func:`_toon_encoded` so the error payload is TOON
    encoded like any other result.
    """
    return _wrap_tool(tool_fn, catch=_BOUNDARY_ERRORS, on_error=_error_payload)


def _bound_arguments(
    signature: inspect.Signature, args: tuple, kwargs: dict
) -> dict[str, Any]:
    """Bind a call's positional/keyword args to names, dropping any ``ctx``."""

    bound = dict(signature.bind_partial(*args, **kwargs).arguments)
    bound.pop("ctx", None)
    return bound


def _event_emitting(
    tool_fn: Callable[..., Any], name: str, registry: Registry
) -> Callable[..., Any]:
    """Wrap a tool callable so a *successful* dispatch emits one agent event.

    This is the sole event producer for the MCP tool surface: applied innermost
    (closest to the real tool), so the event is written only after the tool
    returns — an exception propagates outward, past the emit, and no event is
    recorded. An input-required result (the SEP-2322 ask leg, #664) likewise
    emits nothing: the tool has done no work yet, so the continuation leg is
    the one dispatch that records the single event. The state store is resolved
    from ``registry.state_client`` at call time (never at registration), so
    building a server never forces the SQLite database into existence and a
    test can inject a fake. The emit is guarded by ``try/except`` because
    telemetry must never break a tool call.
    """
    signature = inspect.signature(tool_fn)

    def emit(result: Any, args: tuple, kwargs: dict) -> None:
        if isinstance(result, InputRequiredResult):
            # A SEP-2322 input-required leg (#664) is an ask, not an outcome:
            # recording it as a successful dispatch would write a spurious
            # "ok" event for work that has not happened. The continuation
            # invocation emits the dispatch's single event.
            return
        try:
            _emit_tool_event(
                registry.state_client,
                name,
                _bound_arguments(signature, args, kwargs),
                result,
            )
        except Exception:
            # Telemetry is best-effort: a failing state store (or any hiccup
            # binding/serializing arguments) must never break the tool call.
            pass

    return _wrap_tool(tool_fn, on_success=emit)


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
    :param serve_skills: When True (the default), serve the packaged consulting
        skills (:data:`odoo_sdk.skills.PACKAGED_SKILL_NAMES`) as MCP resources
        (``skill://<name>/SKILL.md`` + ``_manifest``) via fastmcp's
        ``SkillsDirectoryProvider``. Orthogonal to tool gating.
    :type serve_skills: bool
    :param skills_root: Directory of skill folders to serve instead of the
        packaged :func:`odoo_sdk.skills.skills_root`. Defaults to None (serve
        the packaged skills).
    :type skills_root: Optional[Path]
    """

    def __init__(
        self,
        registry: Registry,
        server_name: str = "Odoo MCP Server",
        explicit_tools: Optional[dict[str, ToolSpec]] = None,
        profiling: bool = False,
        instructions: str = DEFAULT_INSTRUCTIONS,
        serve_skills: bool = True,
        skills_root: Optional[Path] = None,
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
        if serve_skills:
            self._register_skills(skills_root)

    def _register_skills(self, root: Optional[Path]) -> None:
        """Serve a directory of skill folders as MCP resources.

        Mounts fastmcp's ``SkillsDirectoryProvider`` (lazily imported — it is
        only paid for when skills are actually served) over ``root``, or over
        the packaged :func:`odoo_sdk.skills.skills_root` when ``root`` is None.
        ``reload=False`` because package data never changes at runtime, and
        ``supporting_files="template"`` keeps the resource list to each skill's
        ``SKILL.md`` + synthetic ``_manifest``. Entirely orthogonal to tool
        gating: the skills are served whether or not the gated tools are.

        :param root: Directory containing skill folders, or None for the
            packaged skills.
        :type root: Optional[Path]
        :return: None.
        :rtype: None
        """
        from fastmcp.server.providers.skills import SkillsDirectoryProvider

        from odoo_sdk.skills import skills_root as packaged_skills_root

        self.mcp.add_provider(
            SkillsDirectoryProvider(
                roots=root if root is not None else packaged_skills_root(),
                reload=False,
                supporting_files="template",
            )
        )

    def _register_tools(self) -> None:
        """Register each explicit tool with the FastMCP server.

        Each tool callable is wrapped in layers, innermost first: an
        :func:`_event_emitting` wrapper that records exactly one ``agent`` event
        per successful dispatch (the sole event producer for the tool surface),
        then an :func:`_error_boundary` that turns caller-actionable exceptions
        into a uniform ``{"error": {...}}`` payload, then :func:`_to_toon` (a
        no-op unless ``ODOO_TOON_OUTPUT`` is set) so that payload TOON-encodes
        like any other result, and — when profiling is enabled — a
        :func:`_profiled` wrapper so every dispatch is captured. Because the
        event wrapper is innermost, a tool that raises propagates its exception
        past the emit (no event) and out to the boundary. All wrappers preserve
        the original typed signature so the wire schema (including any FastMCP
        ``ctx`` parameter) is unchanged.

        :return: None.
        :rtype: None
        """

        for name, spec in self._explicit_tools.items():
            tool_fn, description = self._unpack_spec(spec)
            tool_fn = _event_emitting(tool_fn, name, self.registry)
            tool_fn = _error_boundary(tool_fn)
            tool_fn = _toon_encoded(tool_fn)
            if self.profiling:
                tool_fn = _profiled(tool_fn, name)
            self.mcp.add_tool(
                Tool.from_function(tool_fn, name=name, description=description or None)
            )

    @staticmethod
    def _unpack_spec(spec: ToolSpec) -> Tuple[Callable[..., Any], str]:
        """Normalize a tool spec into a ``(callable, description)`` pair.

        A bare callable (tests pass these) gets an empty description.
        """

        return spec if isinstance(spec, tuple) else (spec, "")

    def run(self, **kwargs: Any) -> None:
        """Start the FastMCP server.

        ``kwargs`` are transport options forwarded to ``FastMCP.run`` (defaults
        to stdio when none are given).
        """

        self.mcp.run(**kwargs)


def _profiled(tool_fn: Callable[..., Any], tool_name: str) -> Callable[..., Any]:
    """Wrap a tool callable so each dispatch is captured with :mod:`cProfile`.

    The profile is dumped in a ``finally`` block so it is still written when the
    tool raises.
    """

    def _start() -> cProfile.Profile:
        profiler = cProfile.Profile()
        profiler.enable()
        return profiler

    def _stop(profiler: cProfile.Profile) -> None:
        profiler.disable()
        _dump_profile(profiler, tool_name)

    return _wrap_tool(tool_fn, setup=_start, teardown=_stop)


def _profile_dir() -> Path:
    """Return the profiling-archive directory, creating it if absent.

    Archives live in a dedicated :data:`PROFILE_SUBDIR` under the system temp
    dir so pruning only ever touches the SDK's own files. The temp dir is
    resolved on every call (not cached) so tests can redirect it.
    """

    directory = Path(tempfile.gettempdir()) / PROFILE_SUBDIR
    directory.mkdir(exist_ok=True)
    return directory


def _prune_profiles(directory: Path) -> None:
    """Delete the oldest archives, retaining at most :data:`PROFILE_KEEP_LAST`.

    Archives are ordered oldest-first by modification time (with the filename as
    a stable tiebreaker); when the directory holds at most that many nothing is
    deleted.
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
    # The clock alone cannot carry uniqueness: ``time.time_ns()`` reports
    # nanoseconds but is only as fine as the platform tick (~15.6 ms on
    # Windows), so back-to-back dumps read an identical value and would
    # overwrite each other. The trailing per-process sequence is what actually
    # guarantees distinct names -- it advances on every call regardless of the
    # clock. Sort order survives because every component is fixed-width and
    # ordered coarse-to-fine: seconds, then nanoseconds, then the counter. The
    # counter is the tiebreak exactly when the two clock fields collide, which
    # is the coarse-tick case above, and zero-padding to PROFILE_SEQ_WIDTH keeps
    # its lexicographic order equal to its numeric order. That matters beyond
    # naming: _prune_profiles breaks st_mtime_ns ties on the filename, so a
    # discriminator that did not sort in creation order (a random uuid4 hex,
    # say) would make pruning delete the wrong archives.
    token = (
        f"{time.strftime('%Y%m%d_%H%M%S')}"
        f"_{time.time_ns() % 1_000_000_000:09d}"
        f"_{next(_profile_seq):0{PROFILE_SEQ_WIDTH}d}"
    )
    prof_path = directory / f"{tool_name}_{token}.prof"
    zip_path = directory / f"odoo_profile_{tool_name}_{token}.zip"

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
