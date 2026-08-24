"""btop-style Textual TUI surface for exploring events and sessions.

This package is a third interaction surface, peer to :mod:`odoo_sdk.cli` and
:mod:`odoo_sdk.mcp`. It visually explores the global, date-queryable sessions
derived from the ``events`` timeseries at query time over a date window, with the
hero view a timeline of session bars per lane so parallel work is visible at a
glance.

The surface composes commands only — it holds no business logic. Every pure
building block (the meter bar, the timeline lane layout, the window controller,
the triage/review line composers, and the state transitions) computes plain data
so it is unit-testable without a terminal. The Textual driver in
:mod:`~odoo_sdk.tui.textual_app` owns the screens, widgets, and key bindings and
is exercised through Textual's headless ``run_test`` pilot.

Layout:

* :mod:`~odoo_sdk.tui.meter` -- meter bar.
* :mod:`~odoo_sdk.tui.timeline` -- session-bar lane layout (the hero view).
* :mod:`~odoo_sdk.tui.window` -- the date-window controller state machine.
* :mod:`~odoo_sdk.tui.triage` -- triage rows and their line composer.
* :mod:`~odoo_sdk.tui.review` -- review cards' line composer.
* :mod:`~odoo_sdk.tui.evidence` -- confidence, citations, and overlap badges.
* :mod:`~odoo_sdk.tui.export` -- the Markdown/CSV export renderers.
* :mod:`~odoo_sdk.tui.app` -- injected deps, app state, and pure transitions.
* :mod:`~odoo_sdk.tui.textual_app` -- the Textual App, screens, and bindings.
"""

from .meter import meter_row
from .timeline import Lane, TimelineGrid, build_timeline
from .window import WINDOW_ACTIONS, DateWindow, apply_action

__all__ = [
    "meter_row",
    "Lane",
    "TimelineGrid",
    "build_timeline",
    "DateWindow",
    "apply_action",
    "WINDOW_ACTIONS",
]
