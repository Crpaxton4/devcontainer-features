from __future__ import annotations

import warnings
from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Final, TypeAlias

from odoo_sdk._utils import _is_sequence

CREATE: Final[int] = 0
UPDATE: Final[int] = 1
DELETE: Final[int] = 2
UNLINK: Final[int] = 3
LINK: Final[int] = 4
CLEAR: Final[int] = 5
SET: Final[int] = 6

_COMMAND_NAMES: Final[dict[int, str]] = {
    CREATE: "create",
    UPDATE: "update",
    DELETE: "delete",
    UNLINK: "unlink",
    LINK: "link",
    CLEAR: "clear",
    SET: "set",
}

X2ManyTupleCommand: TypeAlias = tuple[int, int, Any]


@dataclass(frozen=True, slots=True)
class X2ManyCommand:
    """Represent one validated write-side x2many command.

    Wraps Odoo's positional ``(code, id, payload)`` tuple protocol behind named
    factories, validation, and canonical serialization.
    """

    code: int
    record_id: int = 0
    payload: Any = 0

    @classmethod
    def create(cls, values: Mapping[str, Any]) -> "X2ManyCommand":
        """Build a create command for a new related record."""
        return cls(CREATE, payload=values)

    @classmethod
    def update(cls, record_id: int, values: Mapping[str, Any]) -> "X2ManyCommand":
        """Build an update command for an existing related record."""
        return cls(UPDATE, record_id=record_id, payload=values)

    @classmethod
    def delete(cls, record_id: int) -> "X2ManyCommand":
        """Build a delete command that removes the related record itself."""
        return cls(DELETE, record_id=record_id)

    @classmethod
    def unlink(cls, record_id: int) -> "X2ManyCommand":
        """Build an unlink command that removes the relation only."""
        return cls(UNLINK, record_id=record_id)

    @classmethod
    def link(cls, record_id: int) -> "X2ManyCommand":
        """Build a link command for an existing related record."""
        return cls(LINK, record_id=record_id)

    @classmethod
    def clear(cls) -> "X2ManyCommand":
        """Build a clear command that removes every related id."""
        return cls(CLEAR)

    @classmethod
    def set(cls, ids: Iterable[int]) -> "X2ManyCommand":
        """Build a set command that replaces the full related id set."""
        return cls(SET, payload=ids)

    def __post_init__(self) -> None:
        """Validate and normalize command state after construction.

        :raises ValueError: When the command code or payload shape is invalid.
        """
        if self.code not in _COMMAND_NAMES:
            raise ValueError(f"Unsupported x2many command code: {self.code!r}")
        _COMMAND_STATE_NORMALIZERS[self.code](self)

    def serialize(self) -> X2ManyTupleCommand:
        """Serialize the validated helper into Odoo's tuple command form.

        ``__post_init__`` has already normalized ``record_id`` and ``payload`` into
        their canonical shapes per code, so the wire tuple is a uniform
        ``(code, record_id, payload)`` with the payload copied out defensively: a
        mapping (create/update) is deep-copied, an id iterable (set) becomes a list,
        and the placeholder ``0`` (delete/unlink/link/clear) passes through.
        """
        payload = self.payload
        if isinstance(payload, Mapping):
            payload = deepcopy(dict(payload))
        elif isinstance(payload, tuple):
            payload = list(payload)
        return (self.code, self.record_id, payload)


def normalize_x2many_commands(value: Any) -> list[X2ManyTupleCommand]:
    """Normalize helpers or raw tuples into canonical x2many command tuples.

    Accepts one helper, one raw tuple, or a sequence of either.

    :raises ValueError: When the input cannot be interpreted as valid command data.
    """
    if isinstance(value, X2ManyCommand):
        return [value.serialize()]

    if isinstance(value, tuple):
        return [_normalize_raw_command(value)]

    if _is_sequence(value):
        if not value:
            raise ValueError("x2many command sequences cannot be empty")
        return [_normalize_single_command(item) for item in value]

    raise ValueError(
        "x2many field values must be a helper, raw tuple, or sequence of commands"
    )


def _normalize_single_command(value: Any) -> X2ManyTupleCommand:
    """Normalize one helper or raw tuple item into a canonical command tuple.

    :raises ValueError: When the item is not a supported command shape.
    """
    if isinstance(value, X2ManyCommand):
        return value.serialize()
    if isinstance(value, tuple):
        return _normalize_raw_command(value)
    raise ValueError(
        "x2many command sequences must contain helper objects or raw tuples"
    )


def _normalize_raw_command(command: tuple[Any, ...]) -> X2ManyTupleCommand:
    """Validate and reserialize one raw Odoo command tuple into canonical form.

    :raises ValueError: When the tuple shape or command code is invalid.
    """
    if not command:
        raise ValueError("x2many raw command tuples cannot be empty")

    code = command[0]
    if not _is_command_code(code):
        raise ValueError("x2many raw command tuples must start with an integer code")
    try:
        return _RAW_COMMAND_NORMALIZERS[code](command)
    except KeyError as exc:
        raise ValueError(f"Unsupported x2many command code: {code!r}") from exc


def _normalize_create_state(command: X2ManyCommand) -> None:
    """Normalize state for a create command in place."""
    object.__setattr__(command, "record_id", 0)
    object.__setattr__(
        command,
        "payload",
        _normalize_mapping_payload(command.payload, operation="create"),
    )


def _normalize_update_state(command: X2ManyCommand) -> None:
    """Normalize state for an update command in place."""
    _validate_record_id(command.record_id, operation="update")
    object.__setattr__(
        command,
        "payload",
        _normalize_mapping_payload(command.payload, operation="update"),
    )


def _normalize_relation_id_state(command: X2ManyCommand) -> None:
    """Normalize state for delete, unlink, and link commands in place."""
    _validate_record_id(command.record_id, operation=_COMMAND_NAMES[command.code])
    object.__setattr__(command, "payload", 0)


def _normalize_clear_state(command: X2ManyCommand) -> None:
    """Normalize state for a clear command in place."""
    object.__setattr__(command, "record_id", 0)
    object.__setattr__(command, "payload", 0)


def _normalize_set_state(command: X2ManyCommand) -> None:
    """Normalize state for a set command in place."""
    object.__setattr__(command, "record_id", 0)
    object.__setattr__(
        command,
        "payload",
        _normalize_id_payload(command.payload, operation="set"),
    )


def _normalize_raw_create_command(command: tuple[Any, ...]) -> X2ManyTupleCommand:
    """Validate and normalize a raw create tuple.

    :raises ValueError: When the tuple shape is invalid.
    """
    if len(command) != 3:
        raise ValueError("x2many create tuples must contain exactly 3 items")
    if not _is_placeholder(command[1]):
        raise ValueError("x2many create tuples require 0 as the second item")
    return X2ManyCommand.create(command[2]).serialize()


def _normalize_raw_update_command(command: tuple[Any, ...]) -> X2ManyTupleCommand:
    """Validate and normalize a raw update tuple.

    :raises ValueError: When the tuple shape is invalid.
    """
    if len(command) != 3:
        raise ValueError("x2many update tuples must contain exactly 3 items")
    return X2ManyCommand.update(command[1], command[2]).serialize()


def _normalize_raw_relation_id_command(command: tuple[Any, ...]) -> X2ManyTupleCommand:
    """Validate and normalize a raw delete, unlink, or link tuple.

    :raises ValueError: When the tuple shape or placeholder values are invalid.
    """
    if len(command) not in {2, 3}:
        raise ValueError(
            f"x2many {_COMMAND_NAMES[command[0]]} tuples must contain 2 or 3 items"
        )
    if len(command) == 3 and not _is_placeholder(command[2]):
        raise ValueError(
            f"x2many {_COMMAND_NAMES[command[0]]} tuples require 0 as the third item"
        )
    return X2ManyCommand(command[0], record_id=command[1]).serialize()


def _normalize_raw_clear_command(command: tuple[Any, ...]) -> X2ManyTupleCommand:
    """Validate and normalize a raw clear tuple.

    Odoo tolerates several raw clear tuple lengths (1 to 3 items).

    :raises ValueError: When the tuple uses invalid placeholder values.
    """
    command_length = len(command)
    if command_length > 3:
        raise ValueError("x2many clear tuples must contain between 1 and 3 items")
    if command_length > 1 and not _is_placeholder(command[1]):
        raise ValueError("x2many clear tuples require 0 as the second item")
    if command_length > 2 and not _is_placeholder(command[2]):
        raise ValueError("x2many clear tuples require 0 as the third item")
    return X2ManyCommand.clear().serialize()


def _normalize_raw_set_command(command: tuple[Any, ...]) -> X2ManyTupleCommand:
    """Validate and normalize a raw set tuple.

    :raises ValueError: When the tuple shape is invalid.
    """
    if len(command) != 3:
        raise ValueError("x2many set tuples must contain exactly 3 items")
    if not _is_placeholder(command[1]):
        raise ValueError("x2many set tuples require 0 as the second item")
    return X2ManyCommand.set(command[2]).serialize()


def _normalize_mapping_payload(payload: Any, *, operation: str) -> dict[str, Any]:
    """Validate and deep-copy a mapping payload for create or update commands.

    :raises ValueError: When the payload is not a mapping.
    """
    if not isinstance(payload, Mapping):
        raise ValueError(f"x2many {operation} commands require a mapping payload")
    return deepcopy(dict(payload))


def _normalize_id_payload(payload: Any, *, operation: str) -> tuple[int, ...]:
    """Validate and normalize an iterable payload of related ids into a tuple.

    :raises ValueError: When the payload is not a valid iterable of ids.
    """
    if isinstance(payload, Mapping) or isinstance(payload, (str, bytes, bytearray)):
        raise ValueError(f"x2many {operation} commands require an iterable of ids")

    try:
        raw_ids = tuple(payload)
    except TypeError as exc:
        raise ValueError(
            f"x2many {operation} commands require an iterable of ids"
        ) from exc

    for record_id in raw_ids:
        _validate_record_id(record_id, operation=f"{operation} item")
    return raw_ids


def _validate_record_id(record_id: Any, *, operation: str) -> None:
    """Validate that a command record id is a positive integer.

    :raises ValueError: When the record id is not a positive integer.
    """
    if not _is_record_id(record_id):
        raise ValueError(f"x2many {operation} commands require a positive integer id")


def _is_command_code(value: Any) -> bool:
    """Return whether a value is a supported integer command code (excluding bool)."""
    return isinstance(value, int) and not isinstance(value, bool)


def _is_record_id(value: Any) -> bool:
    """Return whether a value is a positive integer record id (excluding bool)."""
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_placeholder(value: Any) -> bool:
    """Return whether a value is an accepted Odoo placeholder (``0``, ``None``, ``False``)."""
    return value is None or (isinstance(value, int) and not value)


_COMMAND_STATE_NORMALIZERS: Final[dict[int, Any]] = {
    CREATE: _normalize_create_state,
    UPDATE: _normalize_update_state,
    DELETE: _normalize_relation_id_state,
    UNLINK: _normalize_relation_id_state,
    LINK: _normalize_relation_id_state,
    CLEAR: _normalize_clear_state,
    SET: _normalize_set_state,
}

_RAW_COMMAND_NORMALIZERS: Final[dict[int, Any]] = {
    CREATE: _normalize_raw_create_command,
    UPDATE: _normalize_raw_update_command,
    DELETE: _normalize_raw_relation_id_command,
    UNLINK: _normalize_raw_relation_id_command,
    LINK: _normalize_raw_relation_id_command,
    CLEAR: _normalize_raw_clear_command,
    SET: _normalize_raw_set_command,
}


_DEPRECATED_COMMAND_ALIAS = (
    "odoo_sdk.fields.commands.Command is deprecated and will be removed; import "
    "X2ManyCommand instead (renamed to disambiguate it from the command-registry "
    "base odoo_sdk.commands.command.Command)."
)


def __getattr__(name: str) -> Any:
    """Resolve the deprecated ``Command`` alias to :class:`X2ManyCommand` (PEP 562).

    The builder was renamed to :class:`X2ManyCommand` to avoid colliding with the
    command-registry base :class:`odoo_sdk.commands.command.Command`; the old name
    still resolves but emits a :class:`DeprecationWarning`.

    :raises AttributeError: If ``name`` is not a module attribute.
    """
    if name == "Command":
        warnings.warn(_DEPRECATED_COMMAND_ALIAS, DeprecationWarning, stacklevel=2)
        return X2ManyCommand
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "X2ManyCommand",
    "Command",
    "X2ManyTupleCommand",
    "normalize_x2many_commands",
]
