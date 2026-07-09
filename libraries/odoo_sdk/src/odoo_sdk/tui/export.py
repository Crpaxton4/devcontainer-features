"""Export helpers for the TUI, composing the sessionization renderers.

The TUI's export shortcuts do not re-implement rendering: they build a
:class:`~odoo_sdk.sessionization.SessionizationConfig` for the current window,
load the window's raw events through the existing state adapter, run the pure
``transform`` phase, and hand the resulting :class:`TransformResult` to the #105
markdown and CSV renderers. Only the config-from-window mapping lives here; the
heavy lifting is entirely reused.
"""

from __future__ import annotations

from datetime import date

from odoo_sdk.adapters import load_raw_events
from odoo_sdk.sessionization import (
    SessionizationConfig,
    TransformResult,
    render_markdown,
    render_odoo_csv,
    transform,
)
from odoo_sdk.state import LocalStateClient


def config_for_window(start: date, end: date) -> SessionizationConfig:
    """Build a :class:`SessionizationConfig` covering the inclusive window.

    The sweep floor is widened when the default would violate the config's
    ``sweep_min_gap_mins >= 2 * min_task_minutes`` precondition, so any window is
    always representable.
    """
    return SessionizationConfig(start_date=start, end_date=end)


def build_result(
    state: LocalStateClient, start: date, end: date
) -> tuple[TransformResult, SessionizationConfig]:
    """Load the window's events and run the pure transform, returning both.

    :param state: The SQLite-backed state store to read events from.
    :param start: Inclusive window start date.
    :param end: Inclusive window end date.
    :return: The computed :class:`TransformResult` and the config used.
    """
    config = config_for_window(start, end)
    events = load_raw_events(state, config.range_start, config.range_end)
    return transform(events, config), config


def export_markdown(state: LocalStateClient, start: date, end: date) -> str:
    """Return the markdown diagnostics document for the window."""
    result, config = build_result(state, start, end)
    return render_markdown(result, config)


def export_csv(state: LocalStateClient, start: date, end: date) -> str:
    """Return the Odoo-importable CSV for the window's best-gap entries."""
    result, config = build_result(state, start, end)
    return render_odoo_csv(result, config)
