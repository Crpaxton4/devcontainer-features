"""Explicit MCP tool definitions.

Every MCP tool is defined explicitly here — one factory per tool — instead of
auto-reflecting a command's ``execute`` signature. Tools (see :mod:`.atomic`) are
typed wrappers that delegate to a single command.

``TOOL_FACTORIES`` maps a public tool name to a factory
``(service) -> tool callable``. ``build_explicit_tools`` instantiates every tool
bound to a command service, pairing each callable with the description taken from the
like-named command so the wire schema and docs stay in one place. The server
exposes the whole surface: there is no opt-in gate.
"""

from typing import Any, Callable, Dict, Tuple

from odoo_dev_core import CommandService

from .atomic import ATOMIC_TOOL_FACTORIES, atomic_tool

# Full public tool surface: name -> factory(service) -> tool callable.
TOOL_FACTORIES: Dict[str, Callable[[CommandService], Callable[..., Any]]] = {
    **ATOMIC_TOOL_FACTORIES,
}


def build_explicit_tools(
    service: CommandService,
) -> Dict[str, Tuple[Callable[..., Any], str]]:
    """Instantiate every explicit tool bound to ``service``.

    Builds the whole tool surface. Each tool is paired with its description,
    sourced from the like-named command's ``description`` when that command is
    registered.

    :param service: Command service the tools execute through.
    :type service: CommandService
    :return: Mapping of tool name to ``(callable, description)``.
    :rtype: Dict[str, Tuple[Callable[..., Any], str]]
    """
    tools: Dict[str, Tuple[Callable[..., Any], str]] = {}
    for name, factory in TOOL_FACTORIES.items():
        try:
            # ``build_explicit_tools`` is public API for custom registries, so a
            # tool with no like-named command falls back to an empty description.
            description = service.registry.get(name).description
        except KeyError:
            description = ""
        tools[name] = (factory(service), description)
    return tools


__all__ = [
    "TOOL_FACTORIES",
    "ATOMIC_TOOL_FACTORIES",
    "atomic_tool",
    "build_explicit_tools",
]
