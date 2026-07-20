from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any


def as_utc(ts: datetime) -> datetime:
    """Return ``ts`` as an aware UTC datetime.

    The single source for naive→UTC normalization shared across ``utilities/`` and
    ``state/``. A naive datetime is assumed to already be UTC and stamped with the
    UTC timezone; an aware datetime is converted to UTC, so callers get one uniform
    offset for comparison, arithmetic, and string formatting.
    """
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _is_sequence(value: Any) -> bool:
    """Return whether a value is a non-string, non-bytes sequence."""
    if isinstance(value, (str, bytes, bytearray)):
        return False
    return isinstance(value, Sequence)


def _is_null_wire_value(value: Any) -> bool:
    """Return whether a value is a null Odoo wire value (``None``/``False``/``""``)."""
    return value in (None, False, "")


def _dedup_field_names(names: Any) -> list[str]:
    """Return order-preserving unique field names, excluding the synthetic ``id``."""
    return [fn for fn in dict.fromkeys(names) if fn != "id"]
