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
class Command:
    """Represent one validated write-side x2many command.

    This helper type is necessary because Odoo x2many writes rely on positional tuple
    commands that are easy to misuse directly. The SDK wraps them in named factories,
    validation, and canonical serialization before recordset writes reach the server.

    :param code: Odoo x2many command code.
    :type code: int
    :param record_id: Related record id used by update, delete, unlink, and link
        commands, defaults to 0.
    :type record_id: int
    :param payload: Command payload such as values or a set of ids, defaults to 0.
    :type payload: Any
    """

    code: int
    record_id: int = 0
    payload: Any = 0

    @classmethod
    def create(cls, values: Mapping[str, Any]) -> "Command":
        """Build a create command for a new related record.

        This factory is necessary because create commands require a mapping payload and
        a fixed placeholder id in Odoo's tuple protocol.

        :param values: Field values for the related record to create.
        :type values: Mapping[str, Any]
        :return: Validated create command.
        :rtype: Command
        """
        return cls(CREATE, payload=values)

    @classmethod
    def update(cls, record_id: int, values: Mapping[str, Any]) -> "Command":
        """Build an update command for an existing related record.

        This factory is necessary because update commands must carry both a positive
        related id and a mapping payload.

        :param record_id: Related record identifier to update.
        :type record_id: int
        :param values: Field values to write on the related record.
        :type values: Mapping[str, Any]
        :return: Validated update command.
        :rtype: Command
        """
        return cls(UPDATE, record_id=record_id, payload=values)

    @classmethod
    def delete(cls, record_id: int) -> "Command":
        """Build a delete command for a related record.

        This factory is necessary because delete commands remove the related record
        itself and therefore must validate the provided record id.

        :param record_id: Related record identifier to delete.
        :type record_id: int
        :return: Validated delete command.
        :rtype: Command
        """
        return cls(DELETE, record_id=record_id)

    @classmethod
    def unlink(cls, record_id: int) -> "Command":
        """Build an unlink command that removes the relation only.

        This factory is necessary because Odoo distinguishes unlinking a relation from
        deleting the related record itself.

        :param record_id: Related record identifier to unlink.
        :type record_id: int
        :return: Validated unlink command.
        :rtype: Command
        """
        return cls(UNLINK, record_id=record_id)

    @classmethod
    def link(cls, record_id: int) -> "Command":
        """Build a link command for an existing related record.

        This factory is necessary because link commands attach existing related ids to
        the relation without creating new rows.

        :param record_id: Related record identifier to link.
        :type record_id: int
        :return: Validated link command.
        :rtype: Command
        """
        return cls(LINK, record_id=record_id)

    @classmethod
    def clear(cls) -> "Command":
        """Build a clear command that removes every related id.

        This factory is necessary because Odoo uses a distinct command code to clear a
        relation, and callers should not have to remember its raw tuple shape.

        :return: Validated clear command.
        :rtype: Command
        """
        return cls(CLEAR)

    @classmethod
    def set(cls, ids: Iterable[int]) -> "Command":
        """Build a set command that replaces the full related id set.

        This factory is necessary because set commands need iterable id validation and
        a canonical tuple payload before serialization.

        :param ids: Related record ids that should remain linked.
        :type ids: Iterable[int]
        :return: Validated set command.
        :rtype: Command
        """
        return cls(SET, payload=ids)

    def __post_init__(self) -> None:
        """Validate and normalize command state after construction.

        This hook is necessary because each x2many command code has different rules
        for ids and payloads, and invalid state should fail before serialization.

        :raises ValueError: Raised when the command code or payload shape is invalid.
        :return: None.
        :rtype: None
        """
        if self.code not in _COMMAND_NAMES:
            raise ValueError(f"Unsupported x2many command code: {self.code!r}")
        _COMMAND_STATE_NORMALIZERS[self.code](self)

    def serialize(self) -> X2ManyTupleCommand:
        """Serialize the validated helper into Odoo's tuple command form.

        This method is necessary because recordset writes ultimately send raw tuple
        commands over XML-RPC even though callers use a typed helper surface.

        :return: Canonical Odoo x2many tuple command.
        :rtype: X2ManyTupleCommand
        """
        return _COMMAND_SERIALIZERS[self.code](self)


def normalize_x2many_commands(value: Any) -> list[X2ManyTupleCommand]:
    """Normalize helpers or raw tuples into canonical x2many command tuples.

    This entry point is necessary because callers may supply one helper, one raw tuple,
    or a sequence of either, while the write path needs one canonical list form.

    :param value: Helper, raw tuple, or sequence of commands to normalize.
    :type value: Any
    :raises ValueError: Raised when the input cannot be interpreted as valid x2many
        command data.
    :return: Canonical list of Odoo x2many tuple commands.
    :rtype: list[X2ManyTupleCommand]
    """
    if isinstance(value, Command):
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
    """Normalize one helper or raw tuple into a canonical command tuple.

    This helper is necessary because command sequences may mix helper instances and
    raw tuples, but the normalizer still needs one per-item validation path.

    :param value: One sequence item to normalize.
    :type value: Any
    :raises ValueError: Raised when the item is not a supported command shape.
    :return: Canonical Odoo x2many tuple command.
    :rtype: X2ManyTupleCommand
    """
    if isinstance(value, Command):
        return value.serialize()
    if isinstance(value, tuple):
        return _normalize_raw_command(value)
    raise ValueError(
        "x2many command sequences must contain helper objects or raw tuples"
    )


def _normalize_raw_command(command: tuple[Any, ...]) -> X2ManyTupleCommand:
    """Normalize one raw Odoo command tuple into canonical form.

    This helper is necessary because callers may still pass low-level tuple commands,
    but the SDK needs to validate and reserialize them before use.

    :param command: Raw x2many tuple command.
    :type command: tuple[Any, ...]
    :raises ValueError: Raised when the tuple shape or command code is invalid.
    :return: Canonical Odoo x2many tuple command.
    :rtype: X2ManyTupleCommand
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


def _normalize_create_state(command: Command) -> None:
    """Normalize state for a create command.

    This helper is necessary because create commands must discard any provided record
    id and preserve only a mapping payload.

    :param command: Command instance to normalize in place.
    :type command: Command
    :return: None.
    :rtype: None
    """
    object.__setattr__(command, "record_id", 0)
    object.__setattr__(
        command,
        "payload",
        _normalize_mapping_payload(command.payload, operation="create"),
    )


def _normalize_update_state(command: Command) -> None:
    """Normalize state for an update command.

    This helper is necessary because update commands require a positive related id and
    a mapping payload before they can be serialized safely.

    :param command: Command instance to normalize in place.
    :type command: Command
    :return: None.
    :rtype: None
    """
    _validate_record_id(command.record_id, operation="update")
    object.__setattr__(
        command,
        "payload",
        _normalize_mapping_payload(command.payload, operation="update"),
    )


def _normalize_relation_id_state(command: Command) -> None:
    """Normalize state for relation-id-only commands.

    This helper is necessary because delete, unlink, and link commands should carry a
    validated record id and no payload.

    :param command: Command instance to normalize in place.
    :type command: Command
    :return: None.
    :rtype: None
    """
    _validate_record_id(command.record_id, operation=_COMMAND_NAMES[command.code])
    object.__setattr__(command, "payload", 0)


def _normalize_clear_state(command: Command) -> None:
    """Normalize state for a clear command.

    This helper is necessary because clear commands should never retain ids or payload
    data from construction inputs.

    :param command: Command instance to normalize in place.
    :type command: Command
    :return: None.
    :rtype: None
    """
    object.__setattr__(command, "record_id", 0)
    object.__setattr__(command, "payload", 0)


def _normalize_set_state(command: Command) -> None:
    """Normalize state for a set command.

    This helper is necessary because set commands replace the relation with a concrete
    iterable of validated related ids.

    :param command: Command instance to normalize in place.
    :type command: Command
    :return: None.
    :rtype: None
    """
    object.__setattr__(command, "record_id", 0)
    object.__setattr__(
        command,
        "payload",
        _normalize_id_payload(command.payload, operation="set"),
    )


def _serialize_create_command(command: Command) -> X2ManyTupleCommand:
    """Serialize a create helper into Odoo tuple form.

    This helper is necessary because create commands always use placeholder id ``0``
    and a copied mapping payload.

    :param command: Validated create command.
    :type command: Command
    :return: Serialized create tuple.
    :rtype: X2ManyTupleCommand
    """
    return (CREATE, 0, deepcopy(command.payload))


def _serialize_update_command(command: Command) -> X2ManyTupleCommand:
    """Serialize an update helper into Odoo tuple form.

    This helper is necessary because update commands must preserve both the target id
    and a copied mapping payload.

    :param command: Validated update command.
    :type command: Command
    :return: Serialized update tuple.
    :rtype: X2ManyTupleCommand
    """
    return (UPDATE, command.record_id, deepcopy(command.payload))


def _serialize_relation_id_command(command: Command) -> X2ManyTupleCommand:
    """Serialize a delete, unlink, or link helper into tuple form.

    This helper is necessary because relation-id-only commands all share the same raw
    tuple shape once validation has succeeded.

    :param command: Validated relation-id command.
    :type command: Command
    :return: Serialized tuple command.
    :rtype: X2ManyTupleCommand
    """
    return (command.code, command.record_id, 0)


def _serialize_clear_command(command: Command) -> X2ManyTupleCommand:
    """Serialize a clear helper into tuple form.

    This helper is necessary because clear commands always reduce to the same fixed
    tuple regardless of the helper instance state.

    :param command: Validated clear command.
    :type command: Command
    :return: Serialized clear tuple.
    :rtype: X2ManyTupleCommand
    """
    del command
    return (CLEAR, 0, 0)


def _serialize_set_command(command: Command) -> X2ManyTupleCommand:
    """Serialize a set helper into tuple form.

    This helper is necessary because set commands always use placeholder id ``0`` and
    a list payload of related ids.

    :param command: Validated set command.
    :type command: Command
    :return: Serialized set tuple.
    :rtype: X2ManyTupleCommand
    """
    return (SET, 0, list(command.payload))


def _normalize_raw_create_command(command: tuple[Any, ...]) -> X2ManyTupleCommand:
    """Validate and normalize a raw create tuple.

    This helper is necessary because legacy callers may still construct raw create
    tuples directly, and those tuples need validation before reuse.

    :param command: Raw create tuple.
    :type command: tuple[Any, ...]
    :raises ValueError: Raised when the tuple shape is invalid.
    :return: Canonical create tuple command.
    :rtype: X2ManyTupleCommand
    """
    if len(command) != 3:
        raise ValueError("x2many create tuples must contain exactly 3 items")
    if not _is_placeholder(command[1]):
        raise ValueError("x2many create tuples require 0 as the second item")
    return Command.create(command[2]).serialize()


def _normalize_raw_update_command(command: tuple[Any, ...]) -> X2ManyTupleCommand:
    """Validate and normalize a raw update tuple.

    This helper is necessary because raw update tuples must validate their shape and
    target id before entering the write path.

    :param command: Raw update tuple.
    :type command: tuple[Any, ...]
    :raises ValueError: Raised when the tuple shape is invalid.
    :return: Canonical update tuple command.
    :rtype: X2ManyTupleCommand
    """
    if len(command) != 3:
        raise ValueError("x2many update tuples must contain exactly 3 items")
    return Command.update(command[1], command[2]).serialize()


def _normalize_raw_relation_id_command(command: tuple[Any, ...]) -> X2ManyTupleCommand:
    """Validate and normalize a raw delete, unlink, or link tuple.

    This helper is necessary because relation-id-only commands share similar tuple
    rules and should be canonicalized through one validation path.

    :param command: Raw relation-id command tuple.
    :type command: tuple[Any, ...]
    :raises ValueError: Raised when the tuple shape or placeholder values are invalid.
    :return: Canonical relation-id tuple command.
    :rtype: X2ManyTupleCommand
    """
    if len(command) not in {2, 3}:
        raise ValueError(
            f"x2many {_COMMAND_NAMES[command[0]]} tuples must contain 2 or 3 items"
        )
    if len(command) == 3 and not _is_placeholder(command[2]):
        raise ValueError(
            f"x2many {_COMMAND_NAMES[command[0]]} tuples require 0 as the third item"
        )
    return Command(command[0], record_id=command[1]).serialize()


def _normalize_raw_clear_command(command: tuple[Any, ...]) -> X2ManyTupleCommand:
    """Validate and normalize a raw clear tuple.

    This helper is necessary because Odoo tolerates several raw clear tuple lengths,
    but the SDK stores one canonical clear command shape.

    :param command: Raw clear tuple.
    :type command: tuple[Any, ...]
    :raises ValueError: Raised when the tuple uses invalid placeholder values.
    :return: Canonical clear tuple command.
    :rtype: X2ManyTupleCommand
    """
    command_length = len(command)
    if command_length > 3:
        raise ValueError("x2many clear tuples must contain between 1 and 3 items")
    if command_length > 1 and not _is_placeholder(command[1]):
        raise ValueError("x2many clear tuples require 0 as the second item")
    if command_length > 2 and not _is_placeholder(command[2]):
        raise ValueError("x2many clear tuples require 0 as the third item")
    return Command.clear().serialize()


def _normalize_raw_set_command(command: tuple[Any, ...]) -> X2ManyTupleCommand:
    """Validate and normalize a raw set tuple.

    This helper is necessary because raw set tuples must validate their placeholder id
    and related id iterable before being used in writes.

    :param command: Raw set tuple.
    :type command: tuple[Any, ...]
    :raises ValueError: Raised when the tuple shape is invalid.
    :return: Canonical set tuple command.
    :rtype: X2ManyTupleCommand
    """
    if len(command) != 3:
        raise ValueError("x2many set tuples must contain exactly 3 items")
    if not _is_placeholder(command[1]):
        raise ValueError("x2many set tuples require 0 as the second item")
    return Command.set(command[2]).serialize()


def _normalize_mapping_payload(payload: Any, *, operation: str) -> dict[str, Any]:
    """Validate and copy a mapping payload for create or update commands.

    This helper is necessary because Odoo create and update tuple commands require a
    mapping payload and should not retain caller-owned mutable state.

    :param payload: Candidate mapping payload.
    :type payload: Any
    :param operation: Logical command operation being normalized.
    :type operation: str
    :raises ValueError: Raised when the payload is not a mapping.
    :return: Deep-copied mapping payload.
    :rtype: dict[str, Any]
    """
    if not isinstance(payload, Mapping):
        raise ValueError(f"x2many {operation} commands require a mapping payload")
    return deepcopy(dict(payload))


def _normalize_id_payload(payload: Any, *, operation: str) -> tuple[int, ...]:
    """Validate and normalize an iterable payload of related ids.

    This helper is necessary because set commands accept arbitrary iterables, but the
    write path needs a concrete tuple of positive integer ids.

    :param payload: Candidate iterable of related ids.
    :type payload: Any
    :param operation: Logical command operation being normalized.
    :type operation: str
    :raises ValueError: Raised when the payload is not a valid iterable of ids.
    :return: Normalized related ids.
    :rtype: tuple[int, ...]
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

    This helper is necessary because x2many command tuples use record ids in multiple
    places and all of them require the same positive-integer constraint.

    :param record_id: Candidate record identifier.
    :type record_id: Any
    :param operation: Logical operation being validated.
    :type operation: str
    :raises ValueError: Raised when the record id is not a positive integer.
    :return: None.
    :rtype: None
    """
    if not _is_record_id(record_id):
        raise ValueError(f"x2many {operation} commands require a positive integer id")


def _is_command_code(value: Any) -> bool:
    """Return whether a value is a supported integer command code.

    This predicate is necessary because bools are integers in Python, but should not
    be accepted as x2many command codes.

    :param value: Candidate command code.
    :type value: Any
    :return: True when the value is an integer command code.
    :rtype: bool
    """
    return isinstance(value, int) and not isinstance(value, bool)


def _is_record_id(value: Any) -> bool:
    """Return whether a value is a valid positive integer record id.

    This predicate is necessary because x2many relation ids must exclude booleans,
    zero, and negative integers.

    :param value: Candidate related record id.
    :type value: Any
    :return: True when the value is a positive integer id.
    :rtype: bool
    """
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_placeholder(value: Any) -> bool:
    """Return whether a value is an accepted Odoo placeholder token.

    This helper is necessary because several raw x2many tuple forms accept ``0``,
    ``None``, or ``False`` as interchangeable placeholders.

    :param value: Candidate placeholder value.
    :type value: Any
    :return: True when the value is an accepted placeholder token.
    :rtype: bool
    """
    if value is None:
        return True
    if isinstance(value, bool):
        return not value
    return isinstance(value, int) and not value


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


__all__ = ["Command", "X2ManyTupleCommand", "normalize_x2many_commands"]
