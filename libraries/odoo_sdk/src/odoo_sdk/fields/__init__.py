import warnings
from typing import Any

from .commands import _DEPRECATED_COMMAND_ALIAS, X2ManyCommand, normalize_x2many_commands
from .values import (
    RelationCollection,
    RelationValue,
    adapt_field_value,
    adapt_record_values,
)

__all__ = [
    "RelationValue",
    "RelationCollection",
    "X2ManyCommand",
    "Command",
    "normalize_x2many_commands",
    "adapt_field_value",
    "adapt_record_values",
]


def __getattr__(name: str) -> Any:
    """Resolve the deprecated ``Command`` alias to :class:`X2ManyCommand` (PEP 562).

    The x2many write-command builder was renamed to :class:`X2ManyCommand` to stop
    its public name colliding with the command-registry base
    :class:`odoo_sdk.commands.command.Command`. The old ``Command`` name still
    resolves here for backward compatibility, but emits a
    :class:`DeprecationWarning`.

    :param name: Attribute requested on the package.
    :type name: str
    :return: :class:`X2ManyCommand` when ``name`` is the deprecated alias.
    :rtype: Any
    :raises AttributeError: If ``name`` is not a package attribute.
    """
    if name == "Command":
        warnings.warn(_DEPRECATED_COMMAND_ALIAS, DeprecationWarning, stacklevel=2)
        return X2ManyCommand
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
