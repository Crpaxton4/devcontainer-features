from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Mapping, Optional, Sequence

from odoo_sdk._utils import _is_null_wire_value


@dataclass(frozen=True)
class RelationValue:
    """Represent one adapted many2one relation returned by the SDK.

    Turns a raw many2one wire payload into a stable Python shape that preserves the
    relation identity and any display label Odoo included.
    """

    model_name: str
    id: int
    label: Optional[str] = None


@dataclass(frozen=True)
class RelationCollection:
    """Represent an adapted ordered collection of x2many related ids.

    A single predictable shape for one2many and many2many read results. Any iterable
    passed as ``ids`` is coerced to an immutable tuple on construction.
    """

    model_name: str
    ids: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        """Coerce stored relation ids into an immutable tuple."""
        object.__setattr__(self, "ids", tuple(self.ids))


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

    Dispatches on the ``type`` key in *field_metadata*; returns *value* unchanged
    when no adapter is registered for the type or when metadata is absent.
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

    Fields absent from *metadata_by_field* are copied as-is; a falsy map skips
    adaptation entirely.
    """
    if not metadata_by_field:
        return dict(record)

    return {
        field_name: adapt_field_value(raw_value, metadata_by_field.get(field_name))
        for field_name, raw_value in record.items()
    }


def _adapt_many2one(value: Any, field_metadata: Mapping[str, Any]) -> Any:
    """Adapt a raw many2one payload into a ``RelationValue``.

    Odoo sends many2one values as ``False`` (empty) or ``[id, "Name"]``; a bare
    integer id is also accepted. Returns ``None`` for empty values and *value*
    unchanged when it cannot be safely interpreted.
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

    Odoo sends one2many/many2many reads as a flat list of integer ids; empty
    results arrive as ``False``, ``None``, or an empty list/tuple. Returns *value*
    unchanged when it cannot be safely interpreted.
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

    return RelationCollection(model_name=relation_model, ids=value)


def _adapt_date(value: Any, _field_metadata: Mapping[str, Any]) -> Any:
    """Adapt a raw ISO date string (``"YYYY-MM-DD"``) into a Python ``date``.

    ``False``/``None`` map to ``None``; values that fail parsing are returned
    unchanged.
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

    Odoo sends datetimes as naive UTC strings (``"YYYY-MM-DD HH:MM:SS"``); this
    attaches an explicit ``timezone.utc`` so callers never handle ambiguous naive
    datetimes. ``False``/``None`` map to ``None``; parse failures pass through.
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

    ``None``/``False`` map to ``None`` and an empty string to ``b""``; base64
    decode failures return the original value rather than raising.
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
    """Return *value* with explicit ``tzinfo=timezone.utc``.

    Naive datetimes are treated as UTC and stamped; aware datetimes are converted.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _is_valid_relation_id(value: Any) -> bool:
    """Return whether a value is a valid relation record id (int, excluding bool)."""
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
