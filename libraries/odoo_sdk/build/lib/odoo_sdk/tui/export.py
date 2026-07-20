"""Export helpers for the TUI, composing the sessionization renderers.

The TUI's export shortcuts share ONE source of truth with billing (issue #404):
the final time entries and the Odoo CSV are projected from the SQL-derived read
path (:meth:`~odoo_sdk.state.LocalStateClient.derive_sessions_overlapping`) — the
same query the live timeline and the upload path bill from — so an exported figure
is exactly what an upload would write. Each derived :class:`SessionWindow` is
billed through the shared :func:`~odoo_sdk.sessionization.billable_seconds` policy
(half-up rounding + per-session floor) and projected onto the render-only
:class:`~odoo_sdk.sessionization.TimeEntry` shape the #105 renderers consume.

The diagnostic gap-sweep tables (a read-only "what gap would be best" analysis)
are still computed in-Python over the window's raw events; that re-windowing is
pinned to the SQL derivation by a parity test, so the analysis and the billed
entries can never disagree.
"""

from __future__ import annotations

from datetime import date, timedelta

from odoo_sdk.adapters import load_raw_events
from odoo_sdk.sessionization import (
    SessionizationConfig,
    TimeEntry,
    TransformResult,
    billable_events,
    billable_seconds,
    render_markdown,
    render_odoo_csv,
    sweep,
)
from odoo_sdk.state import LocalStateClient, SessionWindow


def config_for_window(start: date, end: date) -> SessionizationConfig:
    """Build a :class:`SessionizationConfig` covering the inclusive window.

    The fixed session gap and the billing policy default from ``LocalConfig``, so
    the config re-windows and rounds on exactly the knobs the SQL derivation and
    the upload path use.
    """
    return SessionizationConfig(start_date=start, end_date=end)


def _entry_from_window(
    window: SessionWindow, config: SessionizationConfig
) -> TimeEntry:
    """Project one SQL-derived :class:`SessionWindow` onto a billed entry.

    The window's raw wall-clock span is billed through the shared policy so the
    entry's ``end - start`` equals the hours an upload writes for the session.
    """
    billed = billable_seconds(window.duration_seconds, config)
    return TimeEntry(
        task_id=window.task_id,
        repo=window.repo,
        pr_num=window.pr_num,
        start=window.started_at,
        end=window.started_at + timedelta(seconds=billed),
        label=window.repo,
        strategy_name=window.strategy_name,
        strategy_category=window.category,
    )


def build_result(
    state: LocalStateClient, start: date, end: date
) -> tuple[TransformResult, SessionizationConfig]:
    """Derive the window's sessions and project them into a render result.

    The final entries come straight from ``derive_sessions_overlapping`` (the
    billing source of truth); the sweep tables are a decoupled read-only analysis
    over the same window's raw events.

    :param state: The SQLite-backed state store to read from.
    :param start: Inclusive window start date.
    :param end: Inclusive window end date.
    :return: The computed :class:`TransformResult` and the config used.
    """
    config = config_for_window(start, end)
    windows = state.derive_sessions_overlapping(
        config.range_start, config.range_end, gap_secs=config.session_gap_secs
    )
    entries = [_entry_from_window(window, config) for window in windows]
    events = load_raw_events(state, config.range_start, config.range_end)
    sweep_results = sweep(billable_events(events), config)
    result = TransformResult(
        all_entries=entries,
        best_gap_entries=entries,
        sweep=sweep_results,
        raw_events=events,
    )
    return result, config


def export_markdown(state: LocalStateClient, start: date, end: date) -> str:
    """Return the markdown diagnostics document for the window."""
    result, config = build_result(state, start, end)
    return render_markdown(result, config)


def export_csv(state: LocalStateClient, start: date, end: date) -> str:
    """Return the Odoo-importable CSV for the window's derived entries."""
    result, config = build_result(state, start, end)
    return render_odoo_csv(result, config)
