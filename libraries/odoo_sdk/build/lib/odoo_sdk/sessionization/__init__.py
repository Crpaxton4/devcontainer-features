"""Pure, state-agnostic sessionization ETL for the Odoo SDK.

This package lifts the Transform + Load-to-data core of the former root-level
``timelog.py`` script into a framework module. It deals only in the primitive
data structures it defines: it is unaware of SQLite, GitHub, git, the
filesystem, and MCP. All input is passed in; all output is returned as data
structures or strings.

Layout:

* :mod:`~odoo_sdk.sessionization.models` -- event / entry / result data model.
* :mod:`~odoo_sdk.sessionization.config` -- pure hyperparameter config.
* :mod:`~odoo_sdk.sessionization.windows` -- gap-based window computation + billing.
* :mod:`~odoo_sdk.sessionization.scoring` -- utilisation scoring.
* :mod:`~odoo_sdk.sessionization.transform` -- gap sweep + Transform orchestrator.
* :mod:`~odoo_sdk.sessionization.render_markdown` -- markdown diagnostics (``-> str``).
* :mod:`~odoo_sdk.sessionization.render_csv` -- Odoo CSV rendering (``-> str``).

The former Strategy pattern (interchangeable per-event-type billing algorithms)
is retired (issue #404): the SQL CTE ``derive_sessions_overlapping`` is the single
sessionization algorithm, and :func:`~odoo_sdk.sessionization.compute_windows`
survives only as the diagnostic gap-sweep's in-Python re-windowing, pinned to the
SQL derivation by a parity test.
"""

from .config import SessionizationConfig
from .models import (
    ET,
    EventType,
    RawEvent,
    SweepResults,
    TimeEntry,
    TransformResult,
)
from .render_csv import CSV_COLUMNS, default_description, render_odoo_csv
from .render_markdown import render_markdown
from .scoring import score_day, score_gap
from .transform import (
    billable_events,
    build_window_entries,
    sweep,
    target_day_totals,
    transform,
)
from .windows import billable_seconds, compute_windows

__all__ = [
    "ET",
    "EventType",
    "RawEvent",
    "TimeEntry",
    "SweepResults",
    "TransformResult",
    "SessionizationConfig",
    "compute_windows",
    "billable_seconds",
    "score_gap",
    "score_day",
    "build_window_entries",
    "billable_events",
    "target_day_totals",
    "sweep",
    "transform",
    "render_markdown",
    "render_odoo_csv",
    "default_description",
    "CSV_COLUMNS",
]
