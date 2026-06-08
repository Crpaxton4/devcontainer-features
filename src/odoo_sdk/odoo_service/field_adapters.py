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
    """Adapt one raw field value using `fields_get` metadata.

    This function is necessary because the SDK's shared Phase B adaptation layer must
    interpret Odoo wire values centrally instead of forcing each caller to decode
    relation, temporal, and binary types by hand.

    :param value: Raw field value returned by Odoo.
    :type value: Any
    :param field_metadata: Metadata describing the field, defaults to None.
    :type field_metadata: Optional[Mapping[str, Any]]
    :return: Adapted value when a supported field type is present, otherwise the raw
        input value.
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

    This helper is necessary because recordset reads need one deterministic place to
    apply field adaptation consistently across all supported field types.

    :param record: Raw record mapping returned by Odoo.
    :type record: Mapping[str, Any]
    :param metadata_by_field: Metadata keyed by field name, defaults to None.
    :type metadata_by_field: Optional[Mapping[str, Mapping[str, Any]]]
    :return: Record copy containing adapted values where possible.
    :rtype: dict[str, Any]
    """
    if not metadata_by_field:
        return dict(record)

    return {
        field_name: adapt_field_value(raw_value, metadata_by_field.get(field_name))
        for field_name, raw_value in record.items()
    }


def _adapt_many2one(value: Any, field_metadata: Mapping[str, Any]) -> Any:
    """Adapt a raw many2one payload into a `RelationValue` when possible.

    This helper is necessary because Odoo many2one fields may arrive as ids or
    ``[id, label]`` pairs, and the shared adapter layer needs one stable relation
    object for downstream callers.

    :param value: Raw many2one value returned by Odoo.
    :type value: Any
    :param field_metadata: Metadata describing the relation field.
    :type field_metadata: Mapping[str, Any]
    :return: Adapted relation value, None for empty values, or the original input when
        it cannot be adapted safely.
    :rtype: Any
    """
    if isinstance(value, RelationValue):
        return value

    relation_model = field_metadata.get("relation")

    if not relation_model:
        return value

    if not value:
        return None

    if isinstance(value, int) and not isinstance(value, bool):
        return RelationValue(model_name=relation_model, id=value)

    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return value

    record_id = value[0]
    if not isinstance(record_id, int) or isinstance(record_id, bool):
        return value

    label: str | None = None
    if len(value) > 1 and value[1] not in (None, False, ""):
        label = str(value[1])

    return RelationValue(model_name=relation_model, id=record_id, label=label)


def _adapt_x2many(value: Any, field_metadata: Mapping[str, Any]) -> Any:
    """Adapt raw x2many ids into a `RelationCollection` when possible.

    This helper is necessary because Odoo one2many and many2many reads return ordered
    related ids that the SDK exposes as a dedicated relation collection type.

    :param value: Raw x2many value returned by Odoo.
    :type value: Any
    :param field_metadata: Metadata describing the relation field.
    :type field_metadata: Mapping[str, Any]
    :return: Adapted relation collection, None-like empty collection for empty values,
        or the original input when it cannot be adapted safely.
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
    """Adapt a raw date payload into a Python `date` when possible.

    This helper is necessary because Odoo date fields are transferred as strings, but
    the Phase B semantic layer promises Python date objects for well-formed values.

    :param value: Raw date value returned by Odoo.
    :type value: Any
    :param _field_metadata: Unused metadata placeholder kept for adapter signature
        consistency.
    :type _field_metadata: Mapping[str, Any]
    :return: Adapted date, None for empty values, or the original input when parsing
        fails.
    :rtype: Any
    """
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
    """Adapt a raw datetime payload into a UTC-aware Python `datetime`.

    This helper is necessary because Odoo datetime values arrive as strings or naive
    datetimes, but the Phase B semantic layer standardizes them to explicit UTC.

    :param value: Raw datetime value returned by Odoo.
    :type value: Any
    :param _field_metadata: Unused metadata placeholder kept for adapter signature
        consistency.
    :type _field_metadata: Mapping[str, Any]
    :return: UTC-normalized datetime, None for empty values, or the original input
        when parsing fails.
    :rtype: Any
    """
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
    """Adapt a raw binary payload into `bytes` when it is valid base64.

    This helper is necessary because Odoo binary fields are commonly transferred as
    base64 strings, but downstream callers often need concrete bytes.

    :param value: Raw binary value returned by Odoo.
    :type value: Any
    :param _field_metadata: Unused metadata placeholder kept for adapter signature
        consistency.
    :type _field_metadata: Mapping[str, Any]
    :return: Decoded bytes, None for null values, an empty bytes object for empty
        strings, or the original input when decoding fails.
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
    """Normalize a datetime value to explicit UTC semantics.

    This helper is necessary because Phase B promises deterministic timezone handling
    for adapted datetime values regardless of whether Odoo returned a naive or aware
    timestamp.

    :param value: Datetime value to normalize.
    :type value: datetime
    :return: Datetime with UTC timezone semantics.
    :rtype: datetime
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)

# TODO Tighten typing here. Eliminate `Any`
_ADAPTERS: dict[str, Any] = {
    "many2one": _adapt_many2one,
    "one2many": _adapt_x2many,
    "many2many": _adapt_x2many,
    "date": _adapt_date,
    "datetime": _adapt_datetime,
    "binary": _adapt_binary,
}
