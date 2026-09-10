from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any


def as_utc(ts: datetime) -> datetime:
    """Return ``ts`` as an aware UTC datetime.

    The single source for naive→UTC normalization shared across the helper
    packages and ``state/``. A naive datetime is assumed to already be UTC and stamped with the
    UTC timezone; an aware datetime is converted to UTC, so callers get one uniform
    offset for comparison, arithmetic, and string formatting.
    """
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def format_chatter(chatter: list[dict]) -> str:
    """Render chatter messages (``date``/``author``/``body`` dicts) as plain text.

    Shared-kernel home (#717): a pure primitives-only renderer needed both by
    the MCP prompt builder (:mod:`odoo_sdk.mcp.prompts.messages`, a surface)
    and re-exported by the data-layer chatter services
    (:mod:`odoo_sdk.services.odoo_helpers`), so under ADR-005 it can live only
    where every layer may import it.
    """
    lines: list[str] = []
    for msg in chatter:
        header = (
            f"[{msg.get('date', '')}] {msg.get('author', '')} "
            f"({msg.get('subtype', msg.get('type', ''))})"
        )
        lines.append(header)
        body = msg.get("body", "").strip()
        if body:
            lines.append(body)
        lines.append("")
    return "\n".join(lines).rstrip()


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
