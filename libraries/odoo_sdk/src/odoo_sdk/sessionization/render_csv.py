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
from .models import ET, SessionStrategyConfig, TimeEntry, TransformResult
from .strategies import DEFAULT_SESSION_STRATEGY_CONFIGS

AI_DESCRIPTION_PREFIX = "[/]"

CSV_COLUMNS = [
    "Date",
    "Description",
    "Project/ID",
    "Task/ID",
    "Quantity",
    "Employee/ID",
    "Unit of Measure/ID",
    "Company/ID",
    "Sales Order Item/ID",
]

# A description provider maps a TimeEntry to a (raw, un-prefixed) action phrase.
DescriptionProvider = Callable[[TimeEntry], str]


def _strategy_settings_for_entry(entry: TimeEntry) -> SessionStrategyConfig:
    """Return the configured strategy settings for a :class:`TimeEntry`."""
    for settings in DEFAULT_SESSION_STRATEGY_CONFIGS:
        if settings.name == entry.strategy_name:
            return settings
    return DEFAULT_SESSION_STRATEGY_CONFIGS[0]


def sanitize_description(text: str) -> str:
    """Return CSV-safe description text without the AI prefix marker."""
    text = text.strip()
    if text.startswith(AI_DESCRIPTION_PREFIX):
        text = text[len(AI_DESCRIPTION_PREFIX):].strip()
    text = business_context(text, 300)
    text = re.sub(r'["\r\n,;]+', " ", text)
    return re.sub(r"\s+", " ", text).strip()


def default_description(entry: TimeEntry) -> str:
    """Return the deterministic strategy-based fallback description."""
    settings = _strategy_settings_for_entry(entry)
    action = sanitize_description(settings.fallback_action)
    return f"{AI_DESCRIPTION_PREFIX} {settings.category}: {action}"


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


def entry_to_csv_row(
    entry: TimeEntry,
    config: SessionizationConfig,
    provider: Optional[DescriptionProvider] = None,
) -> dict:
    """Convert one :class:`TimeEntry` to an Odoo-importable CSV row dict."""
    qty = (entry.end - entry.start).total_seconds() / 3600.0
    task_id = entry.task_id if is_numeric_id(entry.task_id) else ""
    return {
        "Date": entry.start.astimezone(ET).strftime("%Y-%m-%d"),
        "Description": _resolve_description(entry, provider),
        "Project/ID": entry.repo,
        "Task/ID": task_id,
        "Quantity": round(qty, 10),
        "Employee/ID": config.odoo_employee_id,
        "Unit of Measure/ID": config.odoo_uom_id,
        "Company/ID": config.odoo_company_id,
        "Sales Order Item/ID": "",
    }


def render_odoo_csv(
    result: TransformResult,
    config: SessionizationConfig,
    descriptions: Optional[Mapping[int, str]] = None,
) -> str:
    """Render best-gap entries to an Odoo-importable CSV string.

    ``descriptions`` optionally maps an entry index (into
    ``result.best_gap_entries``) to a caller-supplied action phrase, letting an
    enrichment adapter inject Claude-generated text without any I/O in the core.
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
        writer.writerow(entry_to_csv_row(entry, config, provider))
    return buffer.getvalue()
