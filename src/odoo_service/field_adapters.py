from __future__ import annotations

import base64
import binascii
from datetime import date, datetime, timezone
from typing import Any, Mapping, Optional, Sequence

from .field_values import RelationCollection, RelationValue


def adapt_field_value(
    value: Any,
    field_metadata: Optional[Mapping[str, Any]],
) -> Any:
    """Adapts one raw field value using fields_get metadata."""
    if not field_metadata:
        return value

    field_type = field_metadata.get("type")
    adapter = _ADAPTERS.get(field_type)
    if adapter is None:
        return value
    return adapter(value, field_metadata)


def adapt_record_values(
    record: Mapping[str, Any],
    metadata_by_field: Optional[Mapping[str, Mapping[str, Any]]],
) -> dict[str, Any]:
    """Adapts all fields in a record with the provided metadata map."""
    if not metadata_by_field:
        return dict(record)

    return {
        field_name: adapt_field_value(raw_value, metadata_by_field.get(field_name))
        for field_name, raw_value in record.items()
    }


def _adapt_many2one(value: Any, field_metadata: Mapping[str, Any]) -> Any:
    if isinstance(value, RelationValue):
        return value

    relation_model = field_metadata.get("relation")
    if not relation_model:
        return value

    if value in (None, False, "", [], (), {}):
        return None

    if isinstance(value, int) and not isinstance(value, bool):
        return RelationValue(model_name=relation_model, id=value)

    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return value

    if not value:
        return None

    record_id = value[0]
    if not isinstance(record_id, int) or isinstance(record_id, bool):
        return value

    label: str | None = None
    if len(value) > 1 and value[1] not in (None, False, ""):
        label = str(value[1])

    return RelationValue(model_name=relation_model, id=record_id, label=label)


def _adapt_x2many(value: Any, field_metadata: Mapping[str, Any]) -> Any:
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
    if isinstance(value, date) and not isinstance(value, datetime):
        return value

    if value in (None, False, ""):
        return None

    if not isinstance(value, str):
        return value

    try:
        return date.fromisoformat(value)
    except ValueError:
        return value


def _adapt_datetime(value: Any, _field_metadata: Mapping[str, Any]) -> Any:
    if isinstance(value, datetime):
        return _normalize_utc(value)

    if value in (None, False, ""):
        return None

    if not isinstance(value, str):
        return value

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value

    return _normalize_utc(parsed)


def _adapt_binary(value: Any, _field_metadata: Mapping[str, Any]) -> Any:
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
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


_ADAPTERS = {
    "many2one": _adapt_many2one,
    "one2many": _adapt_x2many,
    "many2many": _adapt_x2many,
    "date": _adapt_date,
    "datetime": _adapt_datetime,
    "binary": _adapt_binary,
}