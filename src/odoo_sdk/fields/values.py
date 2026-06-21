from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping, Optional, Sequence

from odoo_sdk._utils import _is_null_wire_value


@dataclass(frozen=True)
class RelationValue:
    """Represent one adapted many2one relation returned by the SDK.

    This value object is necessary because Phase B turns raw many2one wire payloads
    into a stable Python-facing shape that preserves relation identity and any display
    label Odoo included.

    :param model_name: Name of the related Odoo model.
    :type model_name: str
    :param id: Identifier of the related record.
    :type id: int
    :param label: Optional display label returned by Odoo, defaults to None.
    :type label: Optional[str]
    """

    model_name: str
    id: int
    label: Optional[str] = None


@dataclass(frozen=True)
class RelationCollection:
    """Represent an adapted ordered collection of x2many related ids.

    This value object is necessary because Phase B needs one predictable Python shape
    for one2many and many2many read results instead of exposing only raw id lists.

    :param model_name: Name of the related Odoo model.
    :type model_name: str
    :param ids: Ordered related record ids, defaults to an empty tuple.
    :type ids: tuple[int, ...]
    """

    model_name: str
    ids: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        """Coerce stored relation ids into an immutable tuple.

        This hook is necessary because callers may pass any iterable, but the adapted
        value object should retain a stable immutable representation.

        :return: None.
        :rtype: None
        """
        object.__setattr__(self, "ids", tuple(self.ids))

    @classmethod
    def from_ids(
        cls,
        model_name: str,
        ids: Iterable[int],
    ) -> "RelationCollection":
        """Build a relation collection from any iterable of ids.

        This constructor helper is necessary because adapter code often receives list-
        like values from Odoo and needs one explicit way to normalize them into the
        immutable relation collection type.

        :param model_name: Name of the related Odoo model.
        :type model_name: str
        :param ids: Iterable of related record ids.
        :type ids: Iterable[int]
        :return: Immutable relation collection containing the provided ids.
        :rtype: RelationCollection
        """
        return cls(model_name=model_name, ids=tuple(ids))


# ---------------------------------------------------------------------------
# Wire-format adaptation
#
# WHY THIS EXISTS
# ---------------
# Odoo communicates over XML-RPC, which has a limited set of native types
# (int, bool, str, list, dict, bytes, datetime).  Fields that carry richer
# semantics are therefore encoded on the wire as simpler primitives:
#
#   - many2one   → False  OR  [id, "Display Name"]
#   - one2many / many2many → [id, id, ...]
#   - date       → "YYYY-MM-DD"   (ISO 8601 string)
#   - datetime   → "YYYY-MM-DD HH:MM:SS"  (naive UTC string)
#   - binary     → base64-encoded string
#
# WHAT IT DOES
# ------------
# These helpers decode each wire shape into the idiomatic Python type that
# the rest of the SDK and callers work with:
#   - RelationValue / RelationCollection  (defined above in this file)
#   - datetime.date / datetime.datetime with explicit UTC timezone
#   - bytes
#
# WHERE IT FITS
# -------------
# The two public entry-points (`adapt_field_value` and `adapt_record_values`)
# are called exclusively by OdooRecordset immediately after raw results arrive
# from the executor.  No other layer touches raw wire values; by the time
# caller code sees a field value it is already in the adapted Python form.
#
# All field types not listed above are pass-through: the raw value is returned
# unchanged, so adding a new field type does not require changes here unless
# the wire encoding needs translation.
# ---------------------------------------------------------------------------


def adapt_field_value(
    value: Any,
    field_metadata: Optional[Mapping[str, Any]],
) -> Any:
    """Adapt one raw field value using ``fields_get`` metadata.

    Dispatches to the appropriate private adapter based on the ``type`` key
    in *field_metadata*.  Returns *value* unchanged when no adapter is
    registered for the given type or when metadata is absent.

    :param value: Raw field value returned by Odoo over XML-RPC.
    :type value: Any
    :param field_metadata: Single-field slice of a ``fields_get`` response,
        or ``None`` when metadata is unavailable.
    :type field_metadata: Optional[Mapping[str, Any]]
    :return: Adapted Python value, or the original *value* when no
        adaptation applies.
    :rtype: Any
    """
    if not field_metadata:
        return value

    field_type = field_metadata.get("type")
    if field_type is None:
        return value

    adapter = _ADAPTERS.get(field_type)
    if adapter is None:
        return value
    return adapter(value, field_metadata)


def adapt_record_values(
    record: Mapping[str, Any],
    metadata_by_field: Optional[Mapping[str, Mapping[str, Any]]],
) -> dict[str, Any]:
    """Adapt every field in one record using a metadata map.

    Applies ``adapt_field_value`` to each key in *record*, looking up the
    corresponding metadata entry by field name.  Fields absent from
    *metadata_by_field* are copied as-is.

    :param record: Raw record mapping returned by Odoo.
    :type record: Mapping[str, Any]
    :param metadata_by_field: ``fields_get`` response keyed by field name,
        or ``None`` to skip adaptation entirely.
    :type metadata_by_field: Optional[Mapping[str, Mapping[str, Any]]]
    :return: New dict with all fields adapted where possible.
    :rtype: dict[str, Any]
    """
    if not metadata_by_field:
        return dict(record)

    return {
        field_name: adapt_field_value(raw_value, metadata_by_field.get(field_name))
        for field_name, raw_value in record.items()
    }


def _adapt_many2one(value: Any, field_metadata: Mapping[str, Any]) -> Any:
    """Adapt a raw many2one payload into a ``RelationValue``.

    Odoo sends many2one values as ``False`` (empty) or ``[id, "Name"]``.
    An integer-only id is also accepted for contexts where only the id is
    returned.

    :param value: Raw many2one value: ``False``, ``int``, or ``[int, str]``.
    :type value: Any
    :param field_metadata: Field metadata; must contain a ``"relation"`` key.
    :type field_metadata: Mapping[str, Any]
    :return: ``RelationValue``, ``None`` for empty values, or *value*
        unchanged when it cannot be safely interpreted.
    :rtype: Any
    """
    if isinstance(value, RelationValue):
        return value

    relation_model = field_metadata.get("relation")
    if not relation_model:
        return value

    if not value:
        return None

    if _is_valid_relation_id(value):
        return RelationValue(model_name=relation_model, id=value)

    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return value

    record_id = value[0]
    if not _is_valid_relation_id(record_id):
        return value

    label: str | None = None
    if len(value) > 1 and value[1] not in (None, False, ""):
        label = str(value[1])

    return RelationValue(model_name=relation_model, id=record_id, label=label)


def _adapt_x2many(value: Any, field_metadata: Mapping[str, Any]) -> Any:
    """Adapt raw x2many ids into a ``RelationCollection``.

    Odoo sends one2many and many2many reads as a flat list of integer ids
    (e.g., ``[1, 4, 7]``).  Empty results arrive as ``False``, ``None``,
    or an empty list/tuple.

    :param value: Raw x2many value: falsy, or a list of ``int``.
    :type value: Any
    :param field_metadata: Field metadata; must contain a ``"relation"`` key.
    :type field_metadata: Mapping[str, Any]
    :return: ``RelationCollection``, or *value* unchanged when it cannot be
        safely interpreted.
    :rtype: Any
    """
    if isinstance(value, RelationCollection):
        return value

    relation_model = field_metadata.get("relation")
    if not relation_model:
        return value

    if value in (None, False, (), []):
        return RelationCollection(model_name=relation_model)

    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return value

    if not all(isinstance(item, int) and not isinstance(item, bool) for item in value):
        return value

    return RelationCollection.from_ids(relation_model, value)


def _adapt_date(value: Any, _field_metadata: Mapping[str, Any]) -> Any:
    """Adapt a raw date string into a Python ``date``.

    Odoo encodes date fields as ISO 8601 strings (``"YYYY-MM-DD"``).
    ``False`` and ``None`` map to ``None``; values that fail parsing are
    returned unchanged.

    :param value: Raw value: ``None``/``False``, ISO date string, or already
        a ``date``.
    :type value: Any
    :param _field_metadata: Unused; present for a consistent adapter
        signature.
    :type _field_metadata: Mapping[str, Any]
    :return: ``datetime.date``, ``None`` for empty values, or *value*
        unchanged on parse failure.
    :rtype: Any
    """
    if isinstance(value, date) and not isinstance(value, datetime):
        return value

    if _is_null_wire_value(value):
        return None

    if not isinstance(value, str):
        return value

    try:
        return date.fromisoformat(value)
    except ValueError:
        return value


def _adapt_datetime(value: Any, _field_metadata: Mapping[str, Any]) -> Any:
    """Adapt a raw datetime string into a UTC-aware Python ``datetime``.

    Odoo sends datetime fields as naive UTC strings (``"YYYY-MM-DD HH:MM:SS"``).
    This adapter parses them and attaches an explicit ``timezone.utc`` so
    callers never handle ambiguous naive datetimes.

    :param value: Raw value: ``None``/``False``, naive/aware ISO datetime
        string, or already a ``datetime``.
    :type value: Any
    :param _field_metadata: Unused; present for a consistent adapter
        signature.
    :type _field_metadata: Mapping[str, Any]
    :return: UTC-aware ``datetime.datetime``, ``None`` for empty values, or
        *value* unchanged on parse failure.
    :rtype: Any
    """
    if isinstance(value, datetime):
        return _normalize_utc(value)

    if _is_null_wire_value(value):
        return None

    if not isinstance(value, str):
        return value

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value

    return _normalize_utc(parsed)


def _adapt_binary(value: Any, _field_metadata: Mapping[str, Any]) -> Any:
    """Adapt a base64-encoded binary string into ``bytes``.

    Odoo transfers binary field contents as base64 strings over XML-RPC.
    This adapter validates and decodes them so callers receive raw ``bytes``
    rather than an encoded string.  Decode failures return the original
    value rather than raising, preserving robustness for unexpected payloads.

    :param value: Raw value: ``None``/``False``, empty string, base64
        string, or already ``bytes``.
    :type value: Any
    :param _field_metadata: Unused; present for a consistent adapter
        signature.
    :type _field_metadata: Mapping[str, Any]
    :return: ``bytes``, ``None`` for null values, ``b""`` for empty strings,
        or *value* unchanged when base64 decoding fails.
    :rtype: Any
    """
    if isinstance(value, bytes):
        return value

    if value is None or value is False:
        return None

    if not isinstance(value, str):
        return value

    if not value:
        return b""

    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return value


def _normalize_utc(value: datetime) -> datetime:
    """Attach or convert to explicit UTC on a ``datetime``.

    Naive datetimes (no tzinfo) from Odoo are treated as UTC and stamped
    accordingly.  Aware datetimes in other zones are converted.

    :param value: Datetime to normalize.
    :type value: datetime
    :return: Equivalent datetime with ``tzinfo=timezone.utc``.
    :rtype: datetime
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _is_valid_relation_id(value: Any) -> bool:
    """Return whether a value is a valid Odoo relation record id.

    This predicate is necessary because ``bool`` is a subclass of ``int`` in Python,
    and Odoo wire values may include ``False`` which must not be treated as id ``0``.

    :param value: Candidate id value to inspect.
    :type value: Any
    :return: True when the value is a positive-compatible integer that is not a bool.
    :rtype: bool
    """
    return isinstance(value, int) and not isinstance(value, bool)


# Dispatch table mapping Odoo field type strings to their adapter functions.
# Only types whose wire encoding differs from the desired Python type are
# listed here.  All other types are passed through unchanged by adapt_field_value.
_ADAPTERS: dict[str, Any] = {
    "many2one": _adapt_many2one,
    "one2many": _adapt_x2many,
    "many2many": _adapt_x2many,
    "date": _adapt_date,
    "datetime": _adapt_datetime,
    "binary": _adapt_binary,
}
