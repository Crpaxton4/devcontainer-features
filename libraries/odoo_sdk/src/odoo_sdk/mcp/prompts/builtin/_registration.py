"""Decorator-driven registration of the built-in MCP prompts.

This leaf module owns the :data:`BUILTIN_PROMPT_FACTORIES` mapping and the
:func:`builtin_prompt` decorator that populates it, mirroring
:func:`odoo_sdk.mcp.tools.atomic.atomic_tool`. Prompt modules import the
decorator *from here* (never from the package ``__init__``) so that decorating a
factory does not re-enter the package while the package is still importing that
module.

Each registered factory is ``(command_registry) -> prompt callable``: the
callable is what :func:`register_builtin_prompts` hands to
``Prompt.from_function``. A prompt that needs no command access simply ignores
its ``command_registry`` argument, keeping the registration interface uniform
with atomic/composition tools. Adding a prompt is then a pure drop-in: decorate
its factory with :func:`builtin_prompt` and add the module to the explicit
import list in :mod:`.__init__` — no edit to a central ``register_*`` body.
"""

from typing import Any, Callable, Dict

from odoo_sdk.commands import Registry

#: Public prompt name -> factory ``(command_registry) -> prompt callable``.
#: Populated at import time by :func:`builtin_prompt`, replacing the formerly
#: hand-maintained ``mcp.add_prompt(...)`` lines in :func:`register_builtin_prompts`.
BUILTIN_PROMPT_FACTORIES: Dict[str, Callable[[Registry], Callable[..., Any]]] = {}

_Factory = Callable[[Registry], Callable[..., Any]]


def builtin_prompt(name: str) -> Callable[[_Factory], _Factory]:
    """Register the decorated factory in :data:`BUILTIN_PROMPT_FACTORIES`.

    Apply this to every built-in prompt factory. ``name`` is the *public prompt
    name*; it is a separate argument from anything the factory body looks up, so
    a prompt may be exposed under a name that differs from its backing command.
    The factory itself is returned unchanged.

    :param name: Public prompt name under which to register the factory.
    :type name: str
    :raises ValueError: If ``name`` is already registered to another factory,
        which would silently drop one prompt from the built-in surface.
    :return: A decorator that registers and returns the factory.
    :rtype: Callable[[_Factory], _Factory]
    """

    def register(factory: _Factory) -> _Factory:
        if name in BUILTIN_PROMPT_FACTORIES:
            raise ValueError(
                f"Duplicate built-in prompt name {name!r}: "
                f"{BUILTIN_PROMPT_FACTORIES[name].__name__} is already registered."
            )
        BUILTIN_PROMPT_FACTORIES[name] = factory
        return factory

    return register
