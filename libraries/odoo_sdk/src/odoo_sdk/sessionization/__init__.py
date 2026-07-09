"""Pure, state-agnostic sessionization ETL for the Odoo SDK.

This package lifts the Transform + Load-to-data core of the former root-level
``timelog.py`` script into a framework module. It deals only in the primitive
data structures it defines: it is unaware of SQLite, GitHub, git, the
filesystem, and MCP. All input is passed in; all output is returned as data
structures or strings.

Layout:

* :mod:`~odoo_sdk.sessionization.models` -- event / entry / result data model.
* :mod:`~odoo_sdk.sessionization.config` -- pure hyperparameter config.
* :mod:`~odoo_sdk.sessionization.windows` -- gap-based window computation.
* :mod:`~odoo_sdk.sessionization.scoring` -- utilisation scoring.
* :mod:`~odoo_sdk.sessionization.strategies` -- the strategy pattern.
* :mod:`~odoo_sdk.sessionization.transform` -- gap sweep + Transform orchestrator.
* :mod:`~odoo_sdk.sessionization.render_markdown` -- markdown diagnostics (``-> str``).
* :mod:`~odoo_sdk.sessionization.render_csv` -- Odoo CSV rendering (``-> str``).
"""

from .config import SessionizationConfig
from .models import (
    ET,
    EventType,
    RawEvent,
    SessionStrategyConfig,
    SweepResults,
    TimeEntry,
    TransformResult,
)
from .incremental import (
    AGENTLESS_REPO_SENTINEL,
    IncrementalResult,
    LinkDelta,
    SessionEvent,
    SessionState,
    group_key,
    rebuild_group,
    resolve_repo,
    sessionize_group,
    with_resolved_repo,
)
from .render_csv import CSV_COLUMNS, default_description, render_odoo_csv
from .render_markdown import render_markdown
from .scoring import score_day, score_gap
from .strategies import (
    DEFAULT_SESSION_STRATEGY_CONFIGS,
    DuplicateStrategyOwnershipError,
    FixedDurationStrategy,
    SessionizationContext,
    SessionizationStrategy,
    StrategyEventGroup,
    WindowedSessionStrategy,
    make_sessionization_context,
    validate_single_strategy_ownership,
)
from .transform import (
    billable_events,
    build_window_entries,
    sweep,
    target_day_totals,
    transform,
)
from .windows import ceil_to_billing_step, compute_windows

__all__ = [
    "ET",
    "EventType",
    "RawEvent",
    "TimeEntry",
    "SessionStrategyConfig",
    "SweepResults",
    "TransformResult",
    "SessionizationConfig",
    "DEFAULT_SESSION_STRATEGY_CONFIGS",
    "SessionizationStrategy",
    "WindowedSessionStrategy",
    "FixedDurationStrategy",
    "SessionizationContext",
    "StrategyEventGroup",
    "make_sessionization_context",
    "validate_single_strategy_ownership",
    "DuplicateStrategyOwnershipError",
    "AGENTLESS_REPO_SENTINEL",
    "SessionEvent",
    "SessionState",
    "LinkDelta",
    "IncrementalResult",
    "sessionize_group",
    "rebuild_group",
    "group_key",
    "resolve_repo",
    "with_resolved_repo",
    "compute_windows",
    "ceil_to_billing_step",
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
