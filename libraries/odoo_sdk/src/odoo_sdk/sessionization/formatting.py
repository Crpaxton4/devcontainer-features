"""Pure formatting helpers shared by the sessionization renderers."""

from __future__ import annotations

import re
from datetime import datetime

from .models import ET


def fmt_et(ts: datetime) -> str:
    """Format a tz-aware timestamp in the day-bucketing zone (issue #378 item 11).

    The trailing token is the resolved zone's abbreviation (``%Z``, e.g. ``CDT``)
    rather than a hardcoded ``ET`` — the zone is config-driven and no longer
    necessarily Eastern, so a fixed label would misreport the clock.
    """
    return ts.astimezone(ET).strftime("%Y-%m-%d %H:%M %Z")


def fmt_duration(secs: int) -> str:
    """Format a non-negative second count as ``Hh Mm``."""
    secs = max(0, secs)
    return f"{secs // 3600}h {(secs % 3600) // 60}m"


def fmt_delta(secs: float) -> str:
    """Format a signed second delta as ``+/-Hh Mm``."""
    whole = int(secs)
    if whole == 0:
        return "0h 0m"
    sign = "+" if whole > 0 else "-"
    whole = abs(whole)
    return f"{sign}{whole // 3600}h {(whole % 3600) // 60}m"


def md_table_header(cols: list[tuple[str, int]]) -> list[str]:
    """Return the header and separator lines for a markdown table."""
    header = "| " + " | ".join(f"{name:<{w}}" for name, w in cols) + " |"
    separator = "| " + " | ".join("-" * w for _, w in cols) + " |"
    return [header, separator]


def is_numeric_id(value: str) -> bool:
    """Return True if ``value`` is a non-empty run of digits."""
    return bool(value) and value.isdigit()


def bounded_context(text: str, max_chars: int = 700) -> str:
    """Return compact one-line context truncated to ``max_chars``."""
    compact = re.sub(r"\s+", " ", text).strip()
    return compact[:max_chars]


def business_context(text: str, max_chars: int = 220) -> str:
    """Return customer-facing context with technical identifiers scrubbed."""
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"\b(?:PR|pull request)\s*#?\d+\b", " ", text, flags=re.I)
    text = re.sub(r"#\d+\b", " ", text)
    text = re.sub(r"\b[0-9a-f]{7,40}\b", " ", text, flags=re.I)
    text = re.sub(r"\b[\w.-]+/[\w.-]+\b", " ", text)
    text = re.sub(
        r"\b(?:branch|commit|merge|review|ticket|issue|task|id)\b",
        " ",
        text,
        flags=re.I,
    )
    text = re.sub(r"\b\d{4,}\b", " ", text)
    return bounded_context(text, max_chars)
