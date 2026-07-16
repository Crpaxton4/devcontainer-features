from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any


def as_utc(ts: datetime) -> datetime:
    """Return ``ts`` as an aware UTC datetime.

    This helper is the single source for the naive→UTC normalization that several
    layers (``utilities/``, ``state/``) previously each re-implemented by hand and
    had to keep behavior-identical. A naive datetime is assumed to already be UTC
    and is stamped with the UTC timezone; an aware datetime is converted to UTC.
    Both forms therefore return the same instant expressed in one uniform offset,
    which callers rely on for offset-safe comparison, arithmetic, and string
    formatting.

    :param ts: Naive or aware datetime to normalize.
    :type ts: datetime
    :return: The same instant as an aware UTC datetime.
    :rtype: datetime
    """
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _is_sequence(value: Any) -> bool:
    """Return whether a value is a non-string, non-bytes sequence.

    This predicate is necessary because several normalization layers accept list-like
    input but must reject strings, bytes, and bytearrays as accidental iterables.

    :param value: Candidate value to inspect.
    :type value: Any
    :return: True when the value is a supported sequence type.
    :rtype: bool
    """
    if isinstance(value, (str, bytes, bytearray)):
        return False
    return isinstance(value, Sequence)


def _is_null_wire_value(value: Any) -> bool:
    """Return whether a value represents a null Odoo wire value.

    Odoo encodes absent scalar fields as ``None``, ``False``, or empty string over
    XML-RPC. This predicate is necessary because multiple adapters share the same
    null-guard check before attempting type-specific parsing.

    :param value: Candidate wire value to test.
    :type value: Any
    :return: True when the value signals a null or absent field.
    :rtype: bool
    """
    return value in (None, False, "")


def _dedup_field_names(names: Any) -> list[str]:
    """Return deduplicated field names, excluding the synthetic ``id`` field.

    This helper is necessary because several record-reading paths build lists of
    field names from mixed sources and must eliminate duplicates while preserving
    order and dropping the non-metadata ``id`` key.

    :param names: Iterable of field names to deduplicate.
    :type names: Any
    :return: Ordered unique field names without ``id``.
    :rtype: list[str]
    """
    return [fn for fn in dict.fromkeys(names) if fn != "id"]
