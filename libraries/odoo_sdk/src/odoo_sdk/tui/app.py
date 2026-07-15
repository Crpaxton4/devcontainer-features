"""Curses driver for the btop-style TUI.

This is the one impure surface: it owns the ``curses`` loop, parses keystrokes,
calls commands through a :class:`~odoo_sdk.commands.Registry`, and blits the rows
returned by the pure frame composer. It holds no business logic — session
detection is the ``query_sessions`` command's job, export reuses the #105
renderers, and upload delegates to the timesheet commands behind a confirm gate.

The genuinely terminal-bound parts (the render loop, raw ``addstr`` blitting, and
the console entry) are marked ``# pragma: no cover``; the command composition,
key handling, and window/session state transitions are pure functions tested
without a terminal.
"""

from __future__ import annotations

import curses
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import Any, Callable, Optional

from odoo_sdk.commands import Registry
from odoo_sdk.utilities.upload import range_bounds, upload_sessions

from .export import export_csv, export_markdown
from .frame import compose_frame
from .window import DateWindow, apply_action

# Real curses key codes mapped to the window controller's action names.
_KEY_ACTIONS = {
    curses.KEY_LEFT: "left",
    curses.KEY_RIGHT: "right",
    curses.KEY_UP: "up",
    curses.KEY_DOWN: "down",
}

_EXPORT_MD_KEY = ord("e")
_EXPORT_CSV_KEY = ord("c")
_UPLOAD_KEY = ord("u")
_RESYNC_KEY = ord("r")
_QUIT_KEYS = (ord("q"), 27)  # q or ESC
_CONFIRM_KEYS = (ord("y"), ord("Y"))


@dataclass(frozen=True)
class AppState:
    """The pure, serializable state the driver renders and mutates.

    :param window: The current inclusive date window.
    :param sessions: The sessions last returned by ``query_sessions``.
    :param status: A transient status line (export path, upload result, errors).
    :param pending_upload: True while the confirm gate awaits a keypress.
    :param empty_hint: Diagnostic line explaining an empty window; set only when
        the last query returned no sessions, otherwise ``""``.
    """

    window: DateWindow
    sessions: list[dict[str, Any]]
    status: str = ""
    pending_upload: bool = False
    empty_hint: str = ""


def default_window(today: Optional[date] = None, span_days: int = 7) -> DateWindow:
    """Return a window ending today (or ``today``) spanning ``span_days`` days."""
    end = today if today is not None else date.today()
    start = end - timedelta(days=max(0, span_days - 1))
    return DateWindow(start, end)


def query_sessions(registry: Registry, window: DateWindow) -> list[dict[str, Any]]:
    """Compose the ``query_sessions`` command for ``window``'s inclusive range.

    This is the only path to sessions: the command detects them globally, so the
    TUI never recomputes boundaries. The result is a list of session dicts with
    embedded events.
    """
    return registry["query_sessions"].execute(
        start_date=window.start_iso(),
        end_date=window.end_iso(),
        include_events=True,
    )


def refresh(registry: Registry, state: AppState) -> AppState:
    """Return ``state`` with its sessions re-queried for the current window.

    When the query returns no sessions the empty window is ambiguous: nothing may
    have happened, or events exist but do not sessionize in this window (wrong
    window, taskless events, or the gap config). ``empty_hint`` surfaces the raw
    counts so the two cases are distinguishable; it is cleared whenever sessions
    are present.
    """
    sessions = query_sessions(registry, state.window)
    hint = _empty_hint(registry, state.window) if not sessions else ""
    return replace(state, sessions=sessions, empty_hint=hint)


def _window_bounds(window: DateWindow) -> tuple[datetime, datetime]:
    """Return the ``[lo, hi)`` datetime bounds the session query covers.

    Delegates to the shared :func:`~odoo_sdk.utilities.upload.range_bounds` so
    the TUI, the ``query_sessions`` command, and the upload sweep all resolve
    one inclusive-date semantic: ``lo`` is midnight of the start day and ``hi``
    is midnight of the day after the end, so the whole end day is counted.
    """
    return range_bounds(window.start_iso(), window.end_iso())


def _empty_hint(registry: Registry, window: DateWindow) -> str:
    """Return a diagnostic line for a window that derived no sessions.

    Reports how many events fall inside the queried window (``0`` means nothing
    happened; ``N>0`` means data exists but does not sessionize here), how many
    task runs are on record overall, and the session gap the deriver uses.
    """
    command = registry["query_sessions"]
    store = command.state
    lo, hi = _window_bounds(window)
    events = store.count_events(lo, hi)
    runs = len(store.get_all_runs())
    gap = command.config.session_gap_mins
    return (
        f"no sessions derivable — {events} events in window, "
        f"{runs} runs recorded, gap={gap}m"
    )


def move_window(registry: Registry, state: AppState, action: str) -> AppState:
    """Apply an arrow ``action`` and re-query only when the window changed."""
    new_window = apply_action(state.window, action)
    if new_window == state.window:
        return state
    moved = replace(state, window=new_window, status="", pending_upload=False)
    return refresh(registry, moved)


def do_export(
    state: AppState,
    registry: Registry,
    kind: str,
    writer: Callable[[str, str], str],
) -> AppState:
    """Render an export via the #105 renderers and write it, updating status.

    :param state: The current app state (its window bounds the export).
    :param registry: Registry supplying the shared local state client.
    :param kind: ``"markdown"`` or ``"csv"``.
    :param writer: Sink taking ``(content, suggested_name)`` and returning the
        path (or label) written, injected so the pure path stays testable.
    :return: The state with a status line describing the export.
    """
    store = registry["query_sessions"].state
    start, end = state.window.start, state.window.end
    if kind == "csv":
        content = export_csv(store, start, end)
        name = f"timelog_{start.isoformat()}_{end.isoformat()}.csv"
    else:
        content = export_markdown(store, start, end)
        name = f"timelog_{start.isoformat()}_{end.isoformat()}.md"
    where = writer(content, name)
    return replace(state, status=f"exported {kind} -> {where}", pending_upload=False)


def request_upload(state: AppState) -> AppState:
    """Arm the confirm gate before any outward-facing timesheet write."""
    session_count = len(state.sessions)
    return replace(
        state,
        pending_upload=True,
        status=f"upload {session_count} session(s)? press y to confirm, any other key to cancel",
    )


def confirm_upload(state: AppState, registry: Registry, confirmed: bool) -> AppState:
    """Resolve the confirm gate: on ``confirmed`` run the upload, else cancel."""
    if not confirmed:
        return replace(state, pending_upload=False, status="upload cancelled")
    uploaded, retired = _upload_sessions(registry, state.sessions, state.window)
    status = f"uploaded {uploaded} session(s)"
    if retired:
        status += f", retired {retired} orphaned upload(s)"
    return replace(state, pending_upload=False, status=status)


def _upload_sessions(
    registry: Registry, sessions: list[dict[str, Any]], window: DateWindow
) -> tuple[int, int]:
    """Bill the derived sessions through the shared upload loop (#354).

    The ``u`` key and the headless ``odoo-sdk upload`` subcommand share the one
    :func:`~odoo_sdk.utilities.upload.upload_sessions` path: it reconciles each
    session through the sole ``account.analytic.line`` hours-writer (idempotent
    per ``session_key``, so a re-run never double-bills) and then runs the
    window-scoped orphan sweep (#353) that zeroes and retires mappings that no
    longer derive. The window's inclusive dates are forwarded (bounds resolved
    inside the shared loop) so the sweep is scoped exactly to what was queried.
    The shared (client, state) pair is resolved off the ``stop_task`` command,
    the same dependencies every registry command shares.

    :return: ``(uploaded, retired)`` counts for the status line.
    """
    stop_cmd = registry["stop_task"]
    result = upload_sessions(
        stop_cmd._client,
        stop_cmd.state,
        sessions,
        start_date=window.start_iso(),
        end_date=window.end_iso(),
    )
    return int(result["uploaded"]), int(result["retired"])


def _source_summary(outcome: dict[str, Any]) -> str:
    """Render one puller's outcome: an inserted count or its skip reason."""
    if "skipped" in outcome:
        return f"skipped ({outcome['skipped']})"
    return f"+{outcome['inserted']}"


def _resync_status(result: dict[str, Any]) -> str:
    """Render the resync status line: per-source inserted counts / skip reasons."""
    if not result:
        return "resync — nothing to do"
    parts = [f"{source}: {_source_summary(outcome)}" for source, outcome in result.items()]
    return "resync — " + ", ".join(parts)


def do_resync(registry: Registry, state: AppState) -> AppState:
    """Run the manual resync, re-query the window, and report per-source counts.

    Reconciles the current repo's events (git commits, GitHub PRs/reviews, Odoo
    chatter) into local state, then refreshes so any newly derivable sessions
    appear immediately, and surfaces each source's inserted count (or skip
    reason) on the status line.
    """
    result = registry["resync"].execute()
    refreshed = refresh(registry, state)
    return replace(refreshed, status=_resync_status(result), pending_upload=False)


def handle_key(
    registry: Registry,
    state: AppState,
    key: int,
    *,
    writer: Callable[[str, str], str],
) -> tuple[AppState, bool]:
    """Advance the app state for one keypress; return ``(state, should_quit)``.

    Pure w.r.t. the terminal: it only calls commands (through ``registry``) and
    the injected ``writer``. The confirm gate takes precedence so an armed upload
    consumes the next key as its yes/no answer.
    """
    if state.pending_upload:
        return (
            confirm_upload(
                registry=registry, state=state, confirmed=key in _CONFIRM_KEYS
            ),
            False,
        )
    if key in _QUIT_KEYS:
        return state, True
    action = _KEY_ACTIONS.get(key)
    if action is not None:
        return move_window(registry, state, action), False
    if key == _EXPORT_MD_KEY:
        return do_export(state, registry, "markdown", writer), False
    if key == _EXPORT_CSV_KEY:
        return do_export(state, registry, "csv", writer), False
    if key == _UPLOAD_KEY:
        return request_upload(state), False
    if key == _RESYNC_KEY:
        return do_resync(registry, state), False
    return state, False


def _file_writer(content: str, name: str) -> str:  # pragma: no cover
    """Write ``content`` to ``name`` in the current directory; return the path."""
    from pathlib import Path

    path = Path.cwd() / name
    path.write_text(content)
    return str(path)


def _draw(stdscr: Any, state: AppState) -> None:  # pragma: no cover
    """Blit the composed frame for ``state`` onto the curses screen."""
    height, width = stdscr.getmaxyx()
    frame = compose_frame(
        state.sessions,
        state.window,
        width,
        max(height - 1, 0),
        empty_hint=state.empty_hint,
    )
    stdscr.erase()
    for row_index, line in enumerate(frame.rows):
        try:
            stdscr.addstr(row_index, 0, line[: width - 1])
        except curses.error:
            pass
    if state.status:
        try:
            stdscr.addstr(height - 1, 0, state.status[: width - 1])
        except curses.error:
            pass
    stdscr.noutrefresh()
    curses.doupdate()


def _loop(stdscr: Any, registry: Registry) -> None:  # pragma: no cover
    """Run the interactive render/read loop until the user quits."""
    curses.curs_set(0)
    stdscr.keypad(True)
    state = refresh(registry, AppState(window=default_window(), sessions=[]))
    while True:
        _draw(stdscr, state)
        key = stdscr.getch()
        if key == curses.KEY_RESIZE:
            continue
        state, should_quit = handle_key(registry, state, key, writer=_file_writer)
        if should_quit:
            break


def run(registry: Registry) -> None:  # pragma: no cover
    """Start the curses TUI bound to ``registry`` and run until quit.

    ``Ctrl+C`` at the blocking ``getch`` surfaces as ``KeyboardInterrupt``; treat
    it as a normal quit. ``curses.wrapper`` already restores the terminal in its
    own ``finally``, so swallowing the interrupt just avoids a noisy traceback.
    """
    try:
        curses.wrapper(_loop, registry)
    except KeyboardInterrupt:
        pass  # Ctrl+C is a normal quit; the terminal is already restored.
