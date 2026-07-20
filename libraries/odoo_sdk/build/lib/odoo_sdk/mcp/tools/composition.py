"""Decorator-driven registration of the MCP composition tools.

Composition tools take the FastMCP ``ctx`` and orchestrate ``ctx.elicit`` /
``ctx.sample`` before delegating to one or more commands (unlike atomic tools,
which are thin typed wrappers over a single command). This leaf module owns the
:data:`COMPOSITION_TOOL_FACTORIES` mapping and the :func:`composition_tool`
decorator that populates it, mirroring :func:`odoo_sdk.mcp.tools.atomic.atomic_tool`.

Composition tool modules import the decorator *from here* (never from the
package ``__init__``) so that decorating a factory does not re-enter the package
while the package is still importing that module. Adding a composition tool is
then a pure drop-in: decorate its factory with :func:`composition_tool` and add
the module to the explicit import list in :mod:`.__init__` — no edit to a central
dict literal, exactly as for atomic tools and built-in commands.
"""

from typing import Any, Callable, Dict

from odoo_sdk.commands import Registry

#: Public MCP tool name -> factory ``(registry) -> tool callable`` for the
#: composition (``ctx``-taking) tools. Populated at import time by
#: :func:`composition_tool`, replacing the formerly hand-maintained dict literal.
COMPOSITION_TOOL_FACTORIES: Dict[str, Callable[[Registry], Callable[..., Any]]] = {}

_Factory = Callable[[Registry], Callable[..., Any]]


def composition_tool(name: str) -> Callable[[_Factory], _Factory]:
    """Register the decorated factory in :data:`COMPOSITION_TOOL_FACTORIES`.

    Apply this to every composition tool factory. ``name`` is the *public MCP
    tool name*, kept a separate argument from any command name the factory body
    looks up (``registry["..."]``), so a tool may be exposed under a name that
    differs from its backing command(s). The factory itself is returned
    unchanged.

    :param name: Public tool name under which to register the factory.
    :type name: str
    :raises ValueError: If ``name`` is already registered to another factory,
        which would silently drop one tool from the composition surface.
    :return: A decorator that registers and returns the factory.
    :rtype: Callable[[_Factory], _Factory]
    """

    def register(factory: _Factory) -> _Factory:
        if name in COMPOSITION_TOOL_FACTORIES:
            raise ValueError(
                f"Duplicate composition tool name {name!r}: "
                f"{COMPOSITION_TOOL_FACTORIES[name].__name__} is already registered."
            )
        COMPOSITION_TOOL_FACTORIES[name] = factory
        return factory

    return register
