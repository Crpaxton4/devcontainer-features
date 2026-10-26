"""Decorator-driven registration of the built-in commands.

This leaf module owns the :data:`BUILTIN_COMMANDS` mapping and the
:func:`builtin_command` decorator that populates it. Command modules import the
decorator *from here* (never from the package ``__init__``) so that decorating a
class does not re-enter the package while the package is still importing that
module.

Adding a built-in command is two edits:

1. Decorate the command class with :func:`builtin_command`.
2. Add its module to the explicit import list in :mod:`.__init__` — importing
   the module runs the decorator (populating :data:`BUILTIN_COMMANDS`) and
   exposes the class as a package attribute.

The import list stays explicit on purpose: no ``pkgutil`` scanning, so the set
of built-ins remains reviewable and stable for mutation testing.
"""

from typing import Dict, Type

from ..command import Command

#: Mapping of public command name (each class's ``_name``) to command class.
#: Populated at import time by :func:`builtin_command`; never edited by hand.
BUILTIN_COMMANDS: Dict[str, Type[Command]] = {}


def builtin_command(cls: Type[Command]) -> Type[Command]:
    """Register ``cls`` in :data:`BUILTIN_COMMANDS` keyed by its ``_name``.

    Apply this decorator to every SDK built-in command class. It reads the
    class's ``_name`` attribute and stores the class under that key, replacing
    the formerly hand-maintained ``BUILTIN_COMMANDS`` dict literal. Registration
    happens once, when the module defining the class is imported.

    :param cls: Command class to register; must define a unique ``_name``.
    :type cls: Type[Command]
    :raises ValueError: If ``_name`` is already registered to another class,
        which would silently drop one command from the built-in surface.
    :return: ``cls`` unchanged, so the decorator is transparent.
    :rtype: Type[Command]
    """

    name = cls._name
    if name in BUILTIN_COMMANDS:
        raise ValueError(
            f"Duplicate built-in command name {name!r}: "
            f"{BUILTIN_COMMANDS[name].__name__} is already registered."
        )
    BUILTIN_COMMANDS[name] = cls
    return cls
