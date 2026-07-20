"""Explicit MCP tool definitions.

Every MCP tool is defined explicitly here — one factory per tool — instead of
auto-reflecting a command's ``execute`` signature. Atomic tools (see
:mod:`.atomic`) are typed wrappers that delegate to a single command; composition
tools (``start_task``, ``stop_task``) additionally orchestrate ``ctx.elicit`` /
``ctx.sample`` before delegating.

``TOOL_FACTORIES`` maps a public tool name to a factory
``(registry) -> tool callable``. ``build_explicit_tools`` instantiates every tool
bound to a registry, pairing each callable with the description taken from the
like-named command so the wire schema and docs stay in one place.

``build_explicit_tools`` always builds the *whole* surface. Which of those tools a
server actually exposes is a separate decision made by :func:`default_tool_surface`
(#512): a large enough tool count trips Claude Code's client-side lazy-deferral
heuristic, so the default MCP surface is the everyday working set and the
narrow-context tools in :data:`GATED_TOOL_NAMES` (tracker-run administration,
state maintenance/reconciliation, session triage, low-level introspection) are
held back behind an opt-in flag. Nothing is deleted — every gated tool is still
built, still reachable on the CLI, and restored to the MCP surface by setting
``ODOO_MCP_INCLUDE_GATED`` (or passing ``include_gated=True``).
"""

import os
from typing import Any, Callable, Dict, FrozenSet, Optional, Tuple

from odoo_sdk.commands import Registry

from .atomic import ATOMIC_TOOL_FACTORIES, atomic_tool
from .composition import COMPOSITION_TOOL_FACTORIES, composition_tool

# Importing these modules runs their ``@composition_tool`` decorators, populating
# COMPOSITION_TOOL_FACTORIES; the names are also re-exported below.
from .start_task import make_start_task_tool
from .stop_task import make_stop_task_tool

# Full public tool surface: name -> factory(registry) -> tool callable.
TOOL_FACTORIES: Dict[str, Callable[[Registry], Callable[..., Any]]] = {
    **ATOMIC_TOOL_FACTORIES,
    **COMPOSITION_TOOL_FACTORIES,
}

#: Environment variable that opts the :data:`GATED_TOOL_NAMES` back onto the MCP
#: surface. Truthy values (``1``/``true``/``yes``/``on``) restore the full 39-tool
#: surface for a session that genuinely needs the maintenance/triage tooling.
GATED_TOOLS_ENV = "ODOO_MCP_INCLUDE_GATED"

#: Tools held back from the default MCP surface (#512). Every name here is a
#: narrow-context tool — it matters only during tracker-run administration, state
#: maintenance/reconciliation, session triage, or low-level Odoo introspection,
#: not during everyday task/project work. Keeping the default surface small
#: enough avoids Claude Code's client-side lazy deferral, which turns every first
#: tool use into an extra schema round-trip once too many tools are exposed. These
#: are *not* removed: they remain in :data:`TOOL_FACTORIES` (so
#: :func:`build_explicit_tools` still builds them), stay reachable on the CLI, and
#: return to the MCP surface via :data:`GATED_TOOLS_ENV` / ``include_gated``.
GATED_TOOL_NAMES: FrozenSet[str] = frozenset(
    {
        # Tracker-run administration (host SQLite tracker DB; operator tooling).
        "discover_runs",
        "abort_run",
        "list_runs",
        "report_runs",
        "stop_run",
        "stop_all",
        # State maintenance & reconciliation.
        "resync",
        "normalize_timesheets",
        "optimize_sessions",
        "assign_event",
        # Session triage.
        "abort_task",
        # Sessionization & audit analysis.
        "query_sessions",
        "unlogged_time_report",
        # Low-level Odoo introspection.
        "get_models",
        "get_mail_status",
        "search_count",
    }
)


def _gated_opt_in() -> bool:
    """Return whether the gated tools are opted back onto the MCP surface.

    Read per call (not at import) so the flag stays togglable per process and
    trivial to exercise from tests, mirroring the server's other env gates.
    """

    return os.environ.get(GATED_TOOLS_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def build_explicit_tools(
    registry: Registry,
) -> Dict[str, Tuple[Callable[..., Any], str]]:
    """Instantiate every explicit tool bound to ``registry``.

    Builds the *whole* tool surface — the default-vs-gated exposure decision is
    :func:`default_tool_surface`'s, not this producer's. Each tool is paired with
    its description, sourced from the like-named command's ``description`` when
    that command is registered.

    :param registry: Command registry the tools compose.
    :type registry: Registry
    :return: Mapping of tool name to ``(callable, description)``.
    :rtype: Dict[str, Tuple[Callable[..., Any], str]]
    """
    tools: Dict[str, Tuple[Callable[..., Any], str]] = {}
    for name, factory in TOOL_FACTORIES.items():
        try:
            # ``build_explicit_tools`` is public API for custom registries, so a
            # tool with no like-named command falls back to an empty description.
            description = registry[name].description
        except KeyError:
            description = ""
        tools[name] = (factory(registry), description)
    return tools


def default_tool_surface(
    tools: Dict[str, Tuple[Callable[..., Any], str]],
    *,
    include_gated: Optional[bool] = None,
) -> Dict[str, Tuple[Callable[..., Any], str]]:
    """Select the subset of ``tools`` a server exposes by default (#512).

    Drops the narrow-context :data:`GATED_TOOL_NAMES` so the everyday surface
    stays small enough to avoid Claude Code's client-side lazy deferral, which
    otherwise makes every first tool use pay for an extra schema round-trip. The
    reduction is exposure-only: ``tools`` (typically :func:`build_explicit_tools`'
    output) still holds every built tool, so a caller that wants the full surface
    opts in rather than losing anything.

    :param tools: The full mapping of tool name to ``(callable, description)``.
    :type tools: Dict[str, Tuple[Callable[..., Any], str]]
    :param include_gated: When ``True`` return ``tools`` unchanged; when ``False``
        drop the gated tools; when ``None`` (default) decide from the
        :data:`GATED_TOOLS_ENV` environment variable.
    :type include_gated: Optional[bool]
    :return: The tools to expose, insertion order preserved.
    :rtype: Dict[str, Tuple[Callable[..., Any], str]]
    """
    if include_gated is None:
        include_gated = _gated_opt_in()
    if include_gated:
        return dict(tools)
    return {name: spec for name, spec in tools.items() if name not in GATED_TOOL_NAMES}


__all__ = [
    "TOOL_FACTORIES",
    "ATOMIC_TOOL_FACTORIES",
    "COMPOSITION_TOOL_FACTORIES",
    "GATED_TOOL_NAMES",
    "GATED_TOOLS_ENV",
    "atomic_tool",
    "composition_tool",
    "build_explicit_tools",
    "default_tool_surface",
    "make_start_task_tool",
    "make_stop_task_tool",
]
