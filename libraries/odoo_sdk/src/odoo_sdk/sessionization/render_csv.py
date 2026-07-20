"""Pure Odoo timesheet CSV rendering for the sessionization ETL.

Renders best-gap :class:`TimeEntry` rows to an Odoo-importable CSV string. The
description is either a caller-supplied value (e.g. from the Claude enrichment
adapter) or a deterministic strategy-based fallback. No I/O and no subprocess
calls happen here; enrichment lives in an adapter.
"""

from __future__ import annotations

import csv
import io
import re
from typing import Callable, Mapping, Optional

from .config import SessionizationConfig
from .formatting import business_context, is_numeric_id
from .models import TimeEntry, TransformResult

AI_DESCRIPTION_PREFIX = "[/]"

# Deterministic fallback action phrase per session category (issue #404). The
# Strategy pattern that once carried these per-row is retired; the two windowed
# categories the SQL derivation labels — ``development`` and ``review`` — keep a
# customer-facing default here, and any unrecognised label degrades to the
# generic phrase rather than raising.
_FALLBACK_ACTIONS = {
    "development": "advanced project implementation",
    "review": "validated project changes",
}
_DEFAULT_FALLBACK_ACTION = "advanced project work"

# The import columns Odoo does *not* derive for itself (issue #498, verified
# against Odoo 18 ``hr_timesheet.create``):
#
# * ``Date`` — required, and its base default is *today*. Sessionization emits
#   historical rows, so dropping this would silently stamp every backfilled row
#   with the import date instead of the date the work happened.
# * ``Description`` — maps to the required ``name``; Odoo's fallback is ``'/'``,
#   which is not what you want in a billing artifact.
# * ``Task/ID`` — the anchor every dropped column derived from.
# * ``Quantity`` — maps to ``unit_amount``.
# * ``Employee/ID`` — Odoo *can* derive it from the importing user, but only
#   conditionally (it raises for archived employees and assumes exactly one
#   active employee across the selected companies). The upload runs unattended,
#   so the artifact stays deterministic by carrying an explicit id.
#
# Dropped: ``Project/ID`` (follows the task, and was being fed a *git repo
# slug*), ``Company/ID`` (overwritten from the task's company), ``Unit of
# Measure/ID`` (resolved from that company's time mode) and ``Sales Order
# Item/ID`` (a stored compute, and only ever emitted empty here).
#
# The ``/ID`` suffix is left exactly as-is. Odoo's import layer distinguishes
# External ID (``Task/ID``) from Database ID (``Task/.id``), and these columns
# carry raw integers, which suggests ``.id`` is the correct spelling — but that
# reasoning is from source only and has not been confirmed against a live import.
# Verify by importing a two-row CSV both ways against a scratch database before
# changing these headers.
CSV_COLUMNS = [
    "Date",
    "Description",
    "Task/ID",
    "Quantity",
    "Employee/ID",
]

# A description provider maps a TimeEntry to a (raw, un-prefixed) action phrase.
DescriptionProvider = Callable[[TimeEntry], str]


def sanitize_description(text: str) -> str:
    """Return CSV-safe description text without the AI prefix marker."""
    text = text.strip()
    if text.startswith(AI_DESCRIPTION_PREFIX):
        text = text[len(AI_DESCRIPTION_PREFIX):].strip()
    text = business_context(text, 300)
    text = re.sub(r'["\r\n,;]+', " ", text)
    return re.sub(r"\s+", " ", text).strip()


def default_description(entry: TimeEntry) -> str:
    """Return the deterministic category-based fallback description."""
    action = sanitize_description(
        _FALLBACK_ACTIONS.get(entry.strategy_name, _DEFAULT_FALLBACK_ACTION)
    )
    category = entry.strategy_category or "Development"
    return f"{AI_DESCRIPTION_PREFIX} {category}: {action}"


def prefixed_description(entry: TimeEntry, action: str) -> str:
    """Return a prefixed strategy/category description for an action phrase."""
    clean = sanitize_description(action)
    if not clean:
        return default_description(entry)
    return f"{AI_DESCRIPTION_PREFIX} {entry.strategy_category}: {clean}"


def _resolve_description(
    entry: TimeEntry, provider: Optional[DescriptionProvider]
) -> str:
    """Return the description for an entry from the provider or the fallback."""
    if provider is None:
        return default_description(entry)
    return prefixed_description(entry, provider(entry))


def resolve_csv_employee_id(
    config: SessionizationConfig, employee_id: Optional[int] = None
) -> str | int:
    """Return the ``Employee/ID`` cell value, or ``""`` when it is unknown.

    The caller-supplied ``employee_id`` (resolved at export time by the
    ``get_employee_id`` command, which delegates to
    :func:`odoo_sdk.billing.timesheet.resolve_employee_id`) wins over the
    ``SessionizationConfig`` override. When neither is set the cell is left
    empty so Odoo falls back to deriving the employee from the importing user —
    an empty cell is recoverable, whereas the constant this used to default to
    silently attributed the hours to somebody else (issue #497).
    """
    resolved = employee_id if employee_id is not None else config.odoo_employee_id
    return "" if resolved is None else resolved


def entry_to_csv_row(
    entry: TimeEntry,
    config: SessionizationConfig,
    provider: Optional[DescriptionProvider] = None,
    employee_id: Optional[int] = None,
) -> dict:
    """Convert one :class:`TimeEntry` to an Odoo-importable CSV row dict."""
    qty = (entry.end - entry.start).total_seconds() / 3600.0
    task_id = entry.task_id if is_numeric_id(entry.task_id) else ""
    return {
        "Date": entry.start.astimezone(config.day_bucket_tz).strftime("%Y-%m-%d"),
        "Description": _resolve_description(entry, provider),
        "Task/ID": task_id,
        "Quantity": round(qty, 10),
        "Employee/ID": resolve_csv_employee_id(config, employee_id),
    }


def render_odoo_csv(
    result: TransformResult,
    config: SessionizationConfig,
    descriptions: Optional[Mapping[int, str]] = None,
    employee_id: Optional[int] = None,
) -> str:
    """Render best-gap entries to an Odoo-importable CSV string.

    ``descriptions`` optionally maps an entry index (into
    ``result.best_gap_entries``) to a caller-supplied action phrase, letting an
    enrichment adapter inject Claude-generated text without any I/O in the core.

    ``employee_id`` is the export-time resolved ``hr.employee`` id stamped onto
    every row. It is injected rather than looked up here because this module is
    pure: the resolver needs an Odoo client, so the surface that owns one (the
    ``get_employee_id`` command) resolves it and hands the integer down.
    """
    provider: Optional[DescriptionProvider] = None
    if descriptions is not None:
        index_by_id = {id(entry): i for i, entry in enumerate(result.best_gap_entries)}

        def provider(entry: TimeEntry) -> str:
            return descriptions.get(index_by_id.get(id(entry), -1), "")

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    for entry in result.best_gap_entries:
        writer.writerow(entry_to_csv_row(entry, config, provider, employee_id))
    return buffer.getvalue()
