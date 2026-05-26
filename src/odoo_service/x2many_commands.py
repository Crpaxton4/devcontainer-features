from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Final, TypeAlias

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
    code: int
    record_id: int = 0
    payload: Any = 0

    @classmethod
    def create(cls, values: Mapping[str, Any]) -> "X2ManyCommand":
        return cls(CREATE, payload=values)

    @classmethod
    def update(cls, record_id: int, values: Mapping[str, Any]) -> "X2ManyCommand":
        return cls(UPDATE, record_id=record_id, payload=values)

    @classmethod
    def delete(cls, record_id: int) -> "X2ManyCommand":
        return cls(DELETE, record_id=record_id)

    @classmethod
    def unlink(cls, record_id: int) -> "X2ManyCommand":
        return cls(UNLINK, record_id=record_id)

    @classmethod
    def link(cls, record_id: int) -> "X2ManyCommand":
        return cls(LINK, record_id=record_id)

    @classmethod
    def clear(cls) -> "X2ManyCommand":
        return cls(CLEAR)

    @classmethod
    def set(cls, ids: Iterable[int]) -> "X2ManyCommand":
        return cls(SET, payload=ids)

    def __post_init__(self) -> None:
        if self.code not in _COMMAND_NAMES:
            raise ValueError(f"Unsupported x2many command code: {self.code!r}")
        _COMMAND_STATE_NORMALIZERS[self.code](self)

    def serialize(self) -> X2ManyTupleCommand:
        return _COMMAND_SERIALIZERS[self.code](self)


def normalize_x2many_commands(value: Any) -> list[X2ManyTupleCommand]:
    if isinstance(value, X2ManyCommand):
        return [value.serialize()]

    if isinstance(value, tuple):
        return [_normalize_raw_command(value)]

    if _is_sequence(value):
        commands = list(value)
        if not commands:
            raise ValueError("x2many command sequences cannot be empty")
        return [_normalize_single_command(item) for item in commands]

    raise ValueError(
        "x2many field values must be a helper, raw tuple, or sequence of commands"
    )


def _normalize_single_command(value: Any) -> X2ManyTupleCommand:
    if isinstance(value, X2ManyCommand):
        return value.serialize()
    if isinstance(value, tuple):
        return _normalize_raw_command(value)
    raise ValueError(
        "x2many command sequences must contain helper objects or raw tuples"
    )


def _normalize_raw_command(command: tuple[Any, ...]) -> X2ManyTupleCommand:
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
    object.__setattr__(command, "record_id", 0)
    object.__setattr__(
        command,
        "payload",
        _normalize_mapping_payload(command.payload, operation="create"),
    )


def _normalize_update_state(command: X2ManyCommand) -> None:
    _validate_record_id(command.record_id, operation="update")
    object.__setattr__(
        command,
        "payload",
        _normalize_mapping_payload(command.payload, operation="update"),
    )


def _normalize_relation_id_state(command: X2ManyCommand) -> None:
    _validate_record_id(command.record_id, operation=_COMMAND_NAMES[command.code])
    object.__setattr__(command, "payload", 0)


def _normalize_clear_state(command: X2ManyCommand) -> None:
    object.__setattr__(command, "record_id", 0)
    object.__setattr__(command, "payload", 0)


def _normalize_set_state(command: X2ManyCommand) -> None:
    object.__setattr__(command, "record_id", 0)
    object.__setattr__(
        command,
        "payload",
        _normalize_id_payload(command.payload, operation="set"),
    )


def _serialize_create_command(command: X2ManyCommand) -> X2ManyTupleCommand:
    return (CREATE, 0, deepcopy(command.payload))


def _serialize_update_command(command: X2ManyCommand) -> X2ManyTupleCommand:
    return (UPDATE, command.record_id, deepcopy(command.payload))


def _serialize_relation_id_command(command: X2ManyCommand) -> X2ManyTupleCommand:
    return (command.code, command.record_id, 0)


def _serialize_clear_command(command: X2ManyCommand) -> X2ManyTupleCommand:
    del command
    return (CLEAR, 0, 0)


def _serialize_set_command(command: X2ManyCommand) -> X2ManyTupleCommand:
    return (SET, 0, list(command.payload))


def _normalize_raw_create_command(command: tuple[Any, ...]) -> X2ManyTupleCommand:
    if len(command) != 3:
        raise ValueError("x2many create tuples must contain exactly 3 items")
    if not _is_placeholder(command[1]):
        raise ValueError("x2many create tuples require 0 as the second item")
    return X2ManyCommand.create(command[2]).serialize()


def _normalize_raw_update_command(command: tuple[Any, ...]) -> X2ManyTupleCommand:
    if len(command) != 3:
        raise ValueError("x2many update tuples must contain exactly 3 items")
    return X2ManyCommand.update(command[1], command[2]).serialize()


def _normalize_raw_relation_id_command(command: tuple[Any, ...]) -> X2ManyTupleCommand:
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
    command_length = len(command)
    if command_length == 1:
        return X2ManyCommand.clear().serialize()
    if command_length == 2:
        if not _is_placeholder(command[1]):
            raise ValueError("x2many clear tuples require 0 as the second item")
        return X2ManyCommand.clear().serialize()
    if command_length == 3:
        if not _is_placeholder(command[1]):
            raise ValueError("x2many clear tuples require 0 as the second item")
        if not _is_placeholder(command[2]):
            raise ValueError("x2many clear tuples require 0 as the third item")
        return X2ManyCommand.clear().serialize()
    raise ValueError("x2many clear tuples must contain between 1 and 3 items")


def _normalize_raw_set_command(command: tuple[Any, ...]) -> X2ManyTupleCommand:
    if len(command) != 3:
        raise ValueError("x2many set tuples must contain exactly 3 items")
    if not _is_placeholder(command[1]):
        raise ValueError("x2many set tuples require 0 as the second item")
    return X2ManyCommand.set(command[2]).serialize()


def _normalize_mapping_payload(
    payload: Any, *, operation: str
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError(f"x2many {operation} commands require a mapping payload")
    return deepcopy(dict(payload))


def _normalize_id_payload(payload: Any, *, operation: str) -> tuple[int, ...]:
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
    if not _is_record_id(record_id):
        raise ValueError(f"x2many {operation} commands require a positive integer id")


def _is_command_code(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_record_id(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_placeholder(value: Any) -> bool:
    if value is None or value is False:
        return True
    return isinstance(value, int) and not isinstance(value, bool) and value == 0


def _is_sequence(value: Any) -> bool:
    if isinstance(value, (str, bytes, bytearray)):
        return False
    return isinstance(value, Sequence)


_COMMAND_STATE_NORMALIZERS: Final[dict[int, Any]] = {
    CREATE: _normalize_create_state,
    UPDATE: _normalize_update_state,
    DELETE: _normalize_relation_id_state,
    UNLINK: _normalize_relation_id_state,
    LINK: _normalize_relation_id_state,
    CLEAR: _normalize_clear_state,
    SET: _normalize_set_state,
}

_COMMAND_SERIALIZERS: Final[dict[int, Any]] = {
    CREATE: _serialize_create_command,
    UPDATE: _serialize_update_command,
    DELETE: _serialize_relation_id_command,
    UNLINK: _serialize_relation_id_command,
    LINK: _serialize_relation_id_command,
    CLEAR: _serialize_clear_command,
    SET: _serialize_set_command,
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


__all__ = ["X2ManyCommand", "X2ManyTupleCommand", "normalize_x2many_commands"]