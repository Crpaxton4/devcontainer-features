"""btop-style curses TUI surface for exploring events and sessions.

This package is a third interaction surface, peer to :mod:`odoo_sdk.cli` and
:mod:`odoo_sdk.mcp`. It visually explores the global, date-queryable sessions
derived from the ``events`` timeseries at query time over a date window, with the
hero view a timeline of session bars per lane so parallel work is visible at a
glance.

The surface composes commands only — it holds no business logic. Every pure
building block (box-panel drawing, meter bars, the timeline lane layout, and the
window controller) computes cell/character grids as plain data so it is
unit-testable without a terminal. The single :mod:`~odoo_sdk.tui.app` driver owns
the raw ``curses`` loop and is excluded from coverage.

Layout:

* :mod:`~odoo_sdk.tui.box` -- rounded box-panel drawing with titles.
* :mod:`~odoo_sdk.tui.meter` -- meter bar.
* :mod:`~odoo_sdk.tui.timeline` -- session-bar lane layout (the hero view).
* :mod:`~odoo_sdk.tui.window` -- the date-window controller state machine.
* :mod:`~odoo_sdk.tui.app` -- the raw curses driver (``# pragma: no cover``).
"""

from .box import BoxChars, ROUNDED, draw_box, place_text
from .frame import Frame, compose_frame
from .meter import meter_row
from .timeline import Lane, TimelineGrid, build_timeline
from .window import WINDOW_ACTIONS, DateWindow, apply_action

__all__ = [
    "BoxChars",
    "ROUNDED",
    "draw_box",
    "place_text",
    "meter_row",
    "Lane",
    "TimelineGrid",
    "build_timeline",
    "Frame",
    "compose_frame",
    "DateWindow",
    "apply_action",
    "WINDOW_ACTIONS",
]
