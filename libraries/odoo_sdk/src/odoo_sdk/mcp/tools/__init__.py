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
"""

from typing import Any, Callable, Dict, Tuple

from odoo_sdk.commands import Registry

from .atomic import ATOMIC_TOOL_FACTORIES, atomic_tool
from .start_task import make_start_task_tool
from .stop_task import make_stop_task_tool

# Composition tools that take the FastMCP ``ctx`` and orchestrate commands.
COMPOSITION_TOOL_FACTORIES: Dict[str, Callable[[Registry], Callable[..., Any]]] = {
    "start_task": make_start_task_tool,
    "stop_task": make_stop_task_tool,
}

# Full public tool surface: name -> factory(registry) -> tool callable.
TOOL_FACTORIES: Dict[str, Callable[[Registry], Callable[..., Any]]] = {
    **ATOMIC_TOOL_FACTORIES,
    **COMPOSITION_TOOL_FACTORIES,
}


def build_explicit_tools(
    registry: Registry,
) -> Dict[str, Tuple[Callable[..., Any], str]]:
    """Instantiate every explicit tool bound to ``registry``.

    Each tool is paired with its description, sourced from the like-named
    command's ``description`` when that command is registered.

    :param registry: Command registry the tools compose.
    :type registry: Registry
    :return: Mapping of tool name to ``(callable, description)``.
    :rtype: Dict[str, Tuple[Callable[..., Any], str]]
    """
    tools: Dict[str, Tuple[Callable[..., Any], str]] = {}
    for name, factory in TOOL_FACTORIES.items():
        description = _description_for(registry, name)
        tools[name] = (factory(registry), description)
    return tools


def _description_for(registry: Registry, name: str) -> str:
    """Return the registered command's description for ``name``, or empty string.

    :param registry: Command registry to consult.
    :type registry: Registry
    :param name: Tool/command name to look up.
    :type name: str
    :return: The command description, or ``""`` when the command is absent.
    :rtype: str
    """
    try:
        return registry[name].description
    except KeyError:
        return ""


__all__ = [
    "TOOL_FACTORIES",
    "ATOMIC_TOOL_FACTORIES",
    "COMPOSITION_TOOL_FACTORIES",
    "atomic_tool",
    "build_explicit_tools",
    "make_start_task_tool",
    "make_stop_task_tool",
]
