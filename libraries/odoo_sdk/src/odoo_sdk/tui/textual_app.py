"""The Textual driver for the btop-style TUI (issue #605).

This is the terminal-bound half of the TUI: a Textual :class:`~textual.app.App`
with one :class:`~textual.screen.Screen` per mode (main timeline, triage,
review). Every keystroke routes through a screen ``BINDINGS`` entry (the arrow
keys are generated straight from the :data:`~odoo_sdk.tui.window.WINDOW_ACTIONS`
data keymap) or a narrow ``on_key`` hook (the upload confirm gate and the triage
digit input), and every binding action calls a pure transition from
:mod:`~odoo_sdk.tui.app` — the screens hold no business logic and never touch
the store or the RPC client directly.

Rendering is deliberately plain: each panel is a :class:`TextPanel` (a
:class:`~textual.widgets.Static` that remembers the plain-text lines it shows),
fed by small pure line composers, so tests can assert on the exact rendered
lines through Textual's ``run_test`` pilot without reaching into terminal
internals. Unlike the old curses composer there is no fixed-size frame grid:
Textual owns layout, clipping, and resize reflow.
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Any, Callable, Optional, Sequence

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Footer, Static

from odoo_sdk.utilities.stats import SessionStats, compute_stats

from .app import (
    _CONFIRM_KEYS,
    AppState,
    TuiDeps,
    _file_writer,
    assign_triage,
    confirm_upload,
    default_window,
    do_export,
    do_resync,
    enter_review,
    enter_triage,
    erase_triage_digit,
    exit_review,
    exit_triage,
    move_review_selection,
    move_triage_selection,
    move_window,
    refresh,
    request_upload,
    toggle_evidence,
    type_triage_digit,
)
from .meter import meter_row
from .review import review_body_lines
from .timeline import build_timeline
from .triage import triage_body_lines
from .window import WINDOW_ACTIONS, DateWindow

# Shown under the empty-state hint so a blank window always names a next step.
_EMPTY_GUIDANCE = "log events via start_task / odoo-sdk log-event, or widen the window"

# The utilization meters render at a fixed width; the stats panel scrolls/clips.
_METER_WIDTH = 24

# Bar width used before the timeline panel has been laid out (size still 0).
_FALLBACK_TIMELINE_WIDTH = 60


def header_text(window: DateWindow, stats: SessionStats) -> str:
    """Return the single-row header summarizing the window and headline counts."""
    span = f"{window.start_iso()} → {window.end_iso()} ({window.days}d)"
    counts = (
        f"{stats.session_count} sessions  {stats.task_count} tasks  "
        f"{stats.total_events} events  {stats.session_hours:.1f}h"
    )
    return f" odoo-tui  {span}   {counts}"


def stat_lines(stats: SessionStats) -> list[str]:
    """Return the stats-panel body lines (label + value + utilization meters)."""
    return [
        f"session hours   {stats.session_hours:>8.2f}",
        f"events/day      {stats.events_per_day:>8.2f}",
        f"events/week     {stats.events_per_week:>8.2f}",
        f"peak parallel   {stats.peak_concurrency:>8d}",
        f"overlap ratio   {stats.overlap_ratio:>8.2f}",
        f"target util   {meter_row(stats.target_utilization, _METER_WIDTH)}",
        f"calendar util {meter_row(stats.calendar_utilization, _METER_WIDTH)}",
    ]


def empty_lines(empty_hint: str) -> list[str]:
    """Return the empty-window body: the diagnostic hint plus a guidance line.

    ``empty_hint`` distinguishes "no data at all" from "data exists but isn't
    derivable in this window"; when absent (e.g. a direct render), the bare
    placeholder stands in.
    """
    return [empty_hint or "(no sessions in window)", _EMPTY_GUIDANCE]


def timeline_lines(
    sessions: Sequence[dict[str, Any]],
    window: DateWindow,
    width: int,
    empty_hint: str = "",
) -> list[str]:
    """Return the hero timeline body: one ``label | bar`` line per lane."""
    # Bind the naive date window to the local timezone so the axis bounds are
    # tz-aware; stored session timestamps carry offsets, and subtracting a naive
    # bound from an aware timestamp raises TypeError (issue #333).
    start = datetime.combine(window.start, time.min).astimezone()
    end = datetime.combine(window.end, time.max).astimezone()
    label_width = min(22, max(8, width // 3))
    bar_width = max(1, width - label_width - 1)
    grid = build_timeline(sessions, start, end, bar_width)
    lines = [
        f"{lane.label[:label_width]:<{label_width}} {lane.row}" for lane in grid.lanes
    ]
    if not lines:
        lines = empty_lines(empty_hint)
    return lines


class TextPanel(Static):
    """A Static panel of plain-text lines that remembers what it renders.

    The lines are rendered through :class:`rich.text.Text` so content is shown
    literally (a ``[STRONG]`` badge is never parsed as markup), and kept on
    ``text_lines`` so tests assert on exact rendered lines without touching
    Textual rendering internals.
    """

    def __init__(self, *, id: Optional[str] = None) -> None:  # noqa: A002
        super().__init__(id=id)
        self.text_lines: list[str] = []

    def set_lines(self, lines: Sequence[str]) -> None:
        """Replace the panel's content with ``lines`` (plain, markup-free)."""
        self.text_lines = list(lines)
        self.update(Text("\n".join(self.text_lines)))


class _StateScreen(Screen):
    """Base for the mode screens: re-render from the app's state on (re)entry."""

    @property
    def tui(self) -> "OdooTuiApp":
        """The running :class:`OdooTuiApp` (typed convenience accessor)."""
        return self.app  # type: ignore[return-value]

    def on_mount(self) -> None:
        """Paint the freshly-mounted screen from the current app state."""
        self.refresh_from_state(self.tui.state)

    def on_screen_resume(self) -> None:
        """Re-paint when a popped screen returns this one to the top."""
        self.refresh_from_state(self.tui.state)

    def refresh_from_state(self, state: AppState) -> None:
        """Render ``state`` into this screen's widgets (subclasses implement)."""
        raise NotImplementedError

    def _set_status(self, state: AppState) -> None:
        """Render the transient status line shared by every mode screen."""
        self.query_one("#status", TextPanel).set_lines([state.status])


class MainScreen(_StateScreen):
    """The hero timeline view: header, timeline + stats panels, status, footer."""

    BINDINGS = [
        # The arrow keys are the WINDOW_ACTIONS data keymap, mapped 1:1 onto
        # bindings: each Textual key name is a WINDOW_ACTIONS action name.
        *(
            Binding(action, f"window('{action}')", method.replace("_", " "))
            for action, method in WINDOW_ACTIONS.items()
        ),
        Binding("e", "export('markdown')", "export md"),
        Binding("c", "export('csv')", "export csv"),
        Binding("u", "upload", "upload"),
        Binding("r", "resync", "resync"),
        Binding("t", "triage", "triage"),
        Binding("v", "review", "review"),
        Binding("q,escape", "quit_app", "quit"),
    ]

    def compose(self) -> ComposeResult:
        """Lay out the header row, the two body panels, status, and footer."""
        yield TextPanel(id="header")
        with Horizontal(id="body"):
            timeline = TextPanel(id="timeline-panel")
            timeline.border_title = "timeline"
            yield timeline
            stats = TextPanel(id="stats-panel")
            stats.border_title = "stats"
            yield stats
        yield TextPanel(id="status")
        yield Footer()

    def refresh_from_state(self, state: AppState) -> None:
        """Render the timeline, stats, header, and status for ``state``."""
        stats = compute_stats(state.sessions)
        self.query_one("#header", TextPanel).set_lines([header_text(state.window, stats)])
        timeline = self.query_one("#timeline-panel", TextPanel)
        inner_width = timeline.size.width - 4  # borders + padding
        if inner_width <= 0:  # not laid out yet; on_resize re-renders with truth
            inner_width = _FALLBACK_TIMELINE_WIDTH
        timeline.set_lines(
            timeline_lines(state.sessions, state.window, inner_width, state.empty_hint)
        )
        self.query_one("#stats-panel", TextPanel).set_lines(stat_lines(stats))
        self._set_status(state)

    def on_resize(self, event: events.Resize) -> None:
        """Re-render on resize so the timeline bars reflow to the new width."""
        self.refresh_from_state(self.tui.state)

    def on_key(self, event: events.Key) -> None:
        """Resolve the armed upload confirm gate before any binding fires.

        While ``pending_upload`` is set the next keystroke is the gate's answer:
        ``y`` confirms, anything else cancels — so the key is consumed here
        (``prevent_default`` suppresses the screen bindings) exactly like the
        curses driver's gate-first dispatch.
        """
        app = self.tui
        if not app.state.pending_upload:
            return
        event.stop()
        event.prevent_default()
        app.apply(
            confirm_upload(app.state, app.deps, confirmed=event.key in _CONFIRM_KEYS)
        )

    def action_window(self, action: str) -> None:
        """Move the date window by one arrow ``action`` and re-query if changed."""
        app = self.tui
        app.apply(move_window(app.deps, app.state, action))

    def action_export(self, kind: str) -> None:
        """Export the window as ``kind`` through the app's injected writer."""
        app = self.tui
        app.apply(do_export(app.state, app.deps, kind, app.export_writer))

    def action_upload(self) -> None:
        """Arm the upload confirm gate."""
        app = self.tui
        app.apply(request_upload(app.state))

    def action_resync(self) -> None:
        """Run the manual resync command and refresh the window."""
        app = self.tui
        app.apply(do_resync(app.deps, app.state))

    def action_triage(self) -> None:
        """Open the triage queue over the current window."""
        app = self.tui
        app.apply(enter_triage(app.deps, app.state))
        app.push_screen(TriageScreen())

    def action_review(self) -> None:
        """Open the review surface over the current window's sessions."""
        app = self.tui
        app.apply(enter_review(app.deps, app.state))
        app.push_screen(ReviewScreen())

    def action_quit_app(self) -> None:
        """Quit the app (only the main screen quits; sub-modes pop back)."""
        self.app.exit()


class TriageScreen(_StateScreen):
    """The unattributed-event queue: select a row, type a task id, assign."""

    BINDINGS = [
        Binding("up", "move(-1)", "up"),
        Binding("down", "move(1)", "down"),
        Binding("s", "move(1)", "skip"),
        Binding("enter", "assign", "assign"),
        Binding("backspace", "erase", "erase", show=False),
        Binding("q,escape", "back", "back"),
    ]

    def compose(self) -> ComposeResult:
        """Lay out the triage header, row list, input echo, status, and footer."""
        yield TextPanel(id="triage-header")
        yield TextPanel(id="triage-list")
        yield TextPanel(id="triage-input")
        yield TextPanel(id="status")
        yield Footer()

    def refresh_from_state(self, state: AppState) -> None:
        """Render the triage rows, the live task-id input, and the status."""
        self.query_one("#triage-header", TextPanel).set_lines(
            [f" triage — {len(state.triage_rows)} unattributed item(s)"]
        )
        self.query_one("#triage-list", TextPanel).set_lines(
            triage_body_lines(state.triage_rows, state.triage_selected)
        )
        self.query_one("#triage-input", TextPanel).set_lines(
            [f" task id > {state.triage_input}"]
        )
        self._set_status(state)

    def on_key(self, event: events.Key) -> None:
        """Accumulate typed digits into the task-id input (a pure UI concern)."""
        if event.character is None or not event.character.isdigit():
            return
        event.stop()
        event.prevent_default()
        app = self.tui
        app.apply(type_triage_digit(app.state, event.character))

    def action_move(self, delta: int) -> None:
        """Move the highlight by ``delta`` (clamped), discarding a typed id."""
        app = self.tui
        app.apply(move_triage_selection(app.state, delta))

    def action_erase(self) -> None:
        """Erase the last typed digit of the task-id input."""
        app = self.tui
        app.apply(erase_triage_digit(app.state))

    def action_assign(self) -> None:
        """Assign the selected series/event to the typed task id."""
        app = self.tui
        app.apply(assign_triage(app.deps, app.state))

    def action_back(self) -> None:
        """Return to the timeline view (triage never quits the app)."""
        app = self.tui
        app.apply(exit_triage(app.state))
        app.pop_screen()


class ReviewScreen(_StateScreen):
    """The read-only review surface: session cards plus an evidence pane."""

    BINDINGS = [
        Binding("up", "move(-1)", "up"),
        Binding("down", "move(1)", "down"),
        Binding("e,enter", "toggle", "evidence"),
        Binding("q,escape", "back", "back"),
    ]

    def compose(self) -> ComposeResult:
        """Lay out the review header, card list, status, and footer."""
        yield TextPanel(id="review-header")
        yield TextPanel(id="review-list")
        yield TextPanel(id="status")
        yield Footer()

    def refresh_from_state(self, state: AppState) -> None:
        """Render the cards (and the open evidence pane) plus the status."""
        self.query_one("#review-header", TextPanel).set_lines(
            [f" review — {len(state.review_cards)} session(s)"]
        )
        self.query_one("#review-list", TextPanel).set_lines(
            review_body_lines(
                state.review_cards, state.review_selected, state.review_expanded
            )
        )
        self._set_status(state)

    def action_move(self, delta: int) -> None:
        """Move the highlight by ``delta`` (clamped), collapsing the open pane."""
        app = self.tui
        app.apply(move_review_selection(app.state, delta))

    def action_toggle(self) -> None:
        """Toggle the selected card's evidence pane."""
        app = self.tui
        app.apply(toggle_evidence(app.state))

    def action_back(self) -> None:
        """Return to the timeline view (review never quits the app)."""
        app = self.tui
        app.apply(exit_review(app.state))
        app.pop_screen()


class OdooTuiApp(App[None]):
    """The Textual app: owns the injected deps and the single ``AppState``.

    State flows one way: a binding action computes the next :class:`AppState`
    through a pure transition and hands it to :meth:`apply`, which stores it and
    re-renders the top screen. Screens read state only through the app, so the
    main/triage/review screens can never drift from the transitions' view of
    the world.
    """

    CSS = """
    #header, #status, #triage-header, #triage-input, #review-header {
        height: 1;
    }
    #body {
        height: 1fr;
    }
    #timeline-panel {
        width: 2fr;
        border: round $primary;
    }
    #stats-panel {
        width: 1fr;
        border: round $primary;
    }
    #triage-list, #review-list {
        height: 1fr;
    }
    """

    def __init__(
        self,
        deps: TuiDeps,
        *,
        export_writer: Callable[[str, str], str] = _file_writer,
        initial_window: Optional[DateWindow] = None,
    ) -> None:
        """Bind the app to its injected ``deps``.

        :param deps: The command registry, client, store, and config bundle.
        :param export_writer: Sink for the export keys, injected so tests never
            write to the working directory.
        :param initial_window: Starting date window (defaults to the last week),
            injectable so tests pin deterministic dates.
        """
        super().__init__()
        self.deps = deps
        self.export_writer = export_writer
        self.state = AppState(
            window=initial_window if initial_window is not None else default_window(),
            sessions=[],
        )

    def on_mount(self) -> None:
        """Run the initial query, then show the timeline screen."""
        self.state = refresh(self.deps, self.state)
        self.push_screen(MainScreen())

    def apply(self, state: AppState) -> None:
        """Store the transition result and re-render the current screen."""
        self.state = state
        screen = self.screen
        if isinstance(screen, _StateScreen):
            screen.refresh_from_state(state)
