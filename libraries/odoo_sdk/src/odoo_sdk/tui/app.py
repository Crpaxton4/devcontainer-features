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
from datetime import date, timedelta
from typing import Any, Callable, Optional

from odoo_sdk.commands import Registry
from odoo_sdk.utilities.timesheet import reconcile

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
_QUIT_KEYS = (ord("q"), 27)  # q or ESC
_CONFIRM_KEYS = (ord("y"), ord("Y"))


@dataclass(frozen=True)
class AppState:
    """The pure, serializable state the driver renders and mutates.

    :param window: The current inclusive date window.
    :param sessions: The sessions last returned by ``query_sessions``.
    :param status: A transient status line (export path, upload result, errors).
    :param pending_upload: True while the confirm gate awaits a keypress.
    """

    window: DateWindow
    sessions: list[dict[str, Any]]
    status: str = ""
    pending_upload: bool = False


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
    """Return ``state`` with its sessions re-queried for the current window."""
    return replace(state, sessions=query_sessions(registry, state.window))


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
    count = _upload_sessions(registry, state.sessions)
    return replace(
        state,
        pending_upload=False,
        status=f"uploaded {count} session(s)",
    )


def _upload_sessions(registry: Registry, sessions: list[dict[str, Any]]) -> int:
    """Reconcile each queried session's hours onto its Odoo anchor timesheet row.

    The unified timesheet module (issue #181) is the sole writer of
    ``account.analytic.line``: this delegates to its idempotent ``reconcile``,
    which upserts the one anchor row per task rather than re-calling
    ``start_task`` + ``stop_task`` (which duplicated placeholder rows, #177).
    A re-run overwrites the same anchor, so it never double-bills. Sessions
    lacking a numeric task id are skipped (they have no Odoo task to bill).
    """
    stop_cmd = registry["stop_task"]
    client, state = stop_cmd._client, stop_cmd.state
    uploaded = 0
    for session in sessions:
        task_id = _numeric_task_id(session.get("task_id"))
        if task_id is None:
            continue
        _reconcile_one(client, state, task_id, session)
        uploaded += 1
    return uploaded


def _reconcile_one(
    client: Any, state: Any, task_id: int, session: dict[str, Any]
) -> None:
    """Reconcile one queried session's hours onto its task's anchor row.

    Delegation only: the surface passes the identity the queried session carries
    (its numeric ``task_id`` and derived ``duration_secs``) straight to the
    unified module's ``reconcile``, which resolves the anchor id (local session
    store then Odoo) and upserts the single row. When the task has no anchor the
    module treats it as a no-op, so the loop is never interrupted.
    """
    elapsed_hours = float(session.get("duration_secs", 0)) / 3600
    description = f"[/] session {session.get('session_id')}"
    reconcile(client, state, task_id, description, elapsed_hours)


def _numeric_task_id(value: Any) -> Optional[int]:
    """Return ``value`` as an int when it is a numeric task id, else None."""
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


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
    frame = compose_frame(state.sessions, state.window, width, max(height - 1, 0))
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
