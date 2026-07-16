"""Curses driver for the btop-style TUI.

This is the one impure surface: it owns the ``curses`` loop, parses keystrokes,
calls commands through a :class:`~odoo_sdk.commands.Registry`, and blits the rows
returned by the pure frame composer. It holds no business logic — session
detection is the ``query_sessions`` command's job, export reuses the #105
renderers, upload delegates to the timesheet commands behind a confirm gate, and
the triage write delegates to the ``assign_event`` command.

Its dependencies — the RPC client, the local state store, and the resolved
config, plus the command registry it composes — are injected once at
construction as a :class:`TuiDeps` bundle (``tui/__main__`` has all of them in
hand). The driver never harvests them off command instances (no reaching into a
command's private ``._client`` or its ``.state`` / ``.config``); every state
mutation goes through a command, so MCP and CLI can share the same operations.

The genuinely terminal-bound parts (the render loop, raw ``addstr`` blitting, and
the console entry) are marked ``# pragma: no cover``; the command composition,
key handling, and window/session state transitions are pure functions tested
without a terminal.
"""

from __future__ import annotations

import curses
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Any, Callable, Optional

from odoo_sdk.commands import Registry
from odoo_sdk.commands.protocols import RpcClient
from odoo_sdk.state import EventRecord, LocalConfig, LocalStateClient
from odoo_sdk.utilities.logged_lines import logged_hours_by_task_day
from odoo_sdk.billing.upload import range_bounds, upload_sessions

from .evidence import ReviewCard, build_review_cards, compute_overlaps
from .export import export_csv, export_markdown
from .frame import compose_frame
from .review import compose_review_frame
from .triage import TriageRow, build_triage_rows, compose_triage_frame
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
_TRIAGE_KEY = ord("t")
_REVIEW_KEY = ord("v")
_SKIP_KEY = ord("s")
_QUIT_KEYS = (ord("q"), 27)  # q or ESC
_CONFIRM_KEYS = (ord("y"), ord("Y"))
_ENTER_KEYS = (ord("\n"), ord("\r"), curses.KEY_ENTER)
_BACKSPACE_KEYS = (curses.KEY_BACKSPACE, 127, 8)

_MODE_MAIN = "main"
_MODE_TRIAGE = "triage"
_MODE_REVIEW = "review"


@dataclass(frozen=True)
class TuiDeps:
    """The driver's injected dependencies, resolved once at construction.

    The read/write side needs the same three peers the command layer uses — the
    RPC ``client``, the local state ``store``, and the resolved ``config`` — plus
    the command ``registry`` it composes. They are handed in explicitly (see
    :func:`odoo_sdk.tui.__main__.main`) rather than harvested off command
    instances, so the driver never touches a command's private ``._client`` or
    reaches into its ``.state`` / ``.config``. The ``store`` is the very object
    the registry shares with every command, so a write routed through a command
    (e.g. ``assign_event``) is immediately visible to the driver's own reads.

    :param registry: Command registry the driver dispatches through.
    :param client: RPC client for the best-effort Odoo reads (logged hours) and
        the upload path.
    :param store: Shared local state client (the same one the commands use).
    :param config: Resolved SDK configuration (e.g. the session gap).
    """

    registry: Registry
    client: RpcClient
    store: LocalStateClient
    config: LocalConfig


@dataclass(frozen=True)
class AppState:
    """The pure, serializable state the driver renders and mutates.

    Most fields are self-describing (see the annotations below); only the
    non-obvious invariants are documented here:

    * ``empty_hint`` is a diagnostic line set only when the last query returned
      no sessions, otherwise ``""``.
    * ``mode`` is ``"main"`` (timeline), ``"triage"`` (the unattributed-event
      queue), or ``"review"``; the ``triage_*`` fields are meaningful only in
      triage mode and the ``review_*`` fields only in review mode.
    """

    window: DateWindow
    sessions: list[dict[str, Any]]
    status: str = ""
    pending_upload: bool = False
    empty_hint: str = ""
    mode: str = _MODE_MAIN
    triage_rows: list[TriageRow] = field(default_factory=list)
    triage_selected: int = 0
    triage_input: str = ""
    review_cards: list[ReviewCard] = field(default_factory=list)
    review_selected: int = 0
    review_expanded: bool = False


def default_window(today: Optional[date] = None, span_days: int = 7) -> DateWindow:
    """Return a window ending today (or ``today``) spanning ``span_days`` days."""
    end = today if today is not None else date.today()
    start = end - timedelta(days=max(0, span_days - 1))
    return DateWindow(start, end)


def query_sessions(deps: TuiDeps, window: DateWindow) -> list[dict[str, Any]]:
    """Compose the ``query_sessions`` command for ``window``'s inclusive range.

    This is the only path to sessions: the command detects them globally, so the
    TUI never recomputes boundaries. The result is a list of session dicts with
    embedded events.
    """
    return deps.registry["query_sessions"].execute(
        start_date=window.start_iso(),
        end_date=window.end_iso(),
        include_events=True,
    )


def refresh(deps: TuiDeps, state: AppState) -> AppState:
    """Return ``state`` with its sessions re-queried for the current window.

    When the query returns no sessions the empty window is ambiguous: nothing may
    have happened, or events exist but do not sessionize in this window (wrong
    window, taskless events, or the gap config). ``empty_hint`` surfaces the raw
    counts so the two cases are distinguishable; it is cleared whenever sessions
    are present.
    """
    sessions = query_sessions(deps, state.window)
    hint = _empty_hint(deps, state.window) if not sessions else ""
    return replace(state, sessions=sessions, empty_hint=hint)


def _empty_hint(deps: TuiDeps, window: DateWindow) -> str:
    """Return a diagnostic line for a window that derived no sessions.

    Reports how many events fall inside the queried window (``0`` means nothing
    happened; ``N>0`` means data exists but does not sessionize here), how many
    task runs are on record overall, and the session gap the deriver uses.
    """
    store = deps.store
    lo, hi = range_bounds(window.start_iso(), window.end_iso())
    events = store.count_events(lo, hi)
    runs = len(store.get_all_runs())
    gap = deps.config.session_gap_mins
    return (
        f"no sessions derivable — {events} events in window, "
        f"{runs} runs recorded, gap={gap}m"
    )


def move_window(deps: TuiDeps, state: AppState, action: str) -> AppState:
    """Apply an arrow ``action`` and re-query only when the window changed."""
    new_window = apply_action(state.window, action)
    if new_window == state.window:
        return state
    moved = replace(state, window=new_window, status="", pending_upload=False)
    return refresh(deps, moved)


def do_export(
    state: AppState,
    deps: TuiDeps,
    kind: str,
    writer: Callable[[str, str], str],
) -> AppState:
    """Render an export via the #105 renderers and write it, updating status.

    :param state: The current app state (its window bounds the export).
    :param deps: Injected dependencies supplying the shared local state store.
    :param kind: ``"markdown"`` or ``"csv"``.
    :param writer: Sink taking ``(content, suggested_name)`` and returning the
        path (or label) written, injected so the pure path stays testable.
    :return: The state with a status line describing the export.
    """
    store = deps.store
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


def confirm_upload(state: AppState, deps: TuiDeps, confirmed: bool) -> AppState:
    """Resolve the confirm gate: on ``confirmed`` run the upload, else cancel."""
    if not confirmed:
        return replace(state, pending_upload=False, status="upload cancelled")
    uploaded, retired = _upload_sessions(deps, state.sessions, state.window)
    status = f"uploaded {uploaded} session(s)"
    if retired:
        status += f", retired {retired} orphaned upload(s)"
    return replace(state, pending_upload=False, status=status)


def _upload_sessions(
    deps: TuiDeps, sessions: list[dict[str, Any]], window: DateWindow
) -> tuple[int, int]:
    """Bill the derived sessions through the shared upload loop (#354).

    The ``u`` key and the headless ``odoo-sdk upload`` subcommand share the one
    :func:`~odoo_sdk.billing.upload.upload_sessions` path: idempotent per
    ``session_key`` (a re-run never double-bills) plus a window-scoped orphan
    sweep (#353) scoped to the inclusive dates forwarded here.

    :return: ``(uploaded, retired)`` counts for the status line.
    """
    result = upload_sessions(
        deps.client,
        deps.store,
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


def do_resync(deps: TuiDeps, state: AppState) -> AppState:
    """Run the manual resync, re-query the window, and report per-source counts.

    Reconciles the current repo's events (git commits, GitHub PRs/reviews, Odoo
    chatter) into local state, then refreshes so any newly derivable sessions
    appear immediately, and surfaces each source's inserted count (or skip
    reason) on the status line.
    """
    result = deps.registry["resync"].execute()
    refreshed = refresh(deps, state)
    return replace(refreshed, status=_resync_status(result), pending_upload=False)


def _load_triage_rows(deps: TuiDeps, window: DateWindow) -> list[TriageRow]:
    """Query the window's unattributed events and collapse them into triage rows.

    Uses the same inclusive-date ``[midnight start, midnight day-after-end)``
    bounds the session query and empty hint use, so triage and the timeline agree
    on what "this window" means.
    """
    store = deps.store
    lo, hi = range_bounds(window.start_iso(), window.end_iso())
    return build_triage_rows(store.get_unattributed_events(lo, hi))


def enter_triage(deps: TuiDeps, state: AppState) -> AppState:
    """Open the triage queue: load the window's unattributed events and select row 0.

    Surfaces every event ingested with ``task_ids=[]`` in the current window so a
    meeting or email that could not be confidently attributed is triaged rather
    than silently never billing (#370, acceptance item 9).
    """
    rows = _load_triage_rows(deps, state.window)
    return replace(
        state,
        mode=_MODE_TRIAGE,
        triage_rows=rows,
        triage_selected=0,
        triage_input="",
        status=f"triage — {len(rows)} unattributed item(s)",
    )


def _exit_triage(state: AppState) -> AppState:
    """Leave the triage queue and return to the timeline view."""
    return replace(state, mode=_MODE_MAIN, triage_input="", status="")


def _move_triage_selection(state: AppState, delta: int) -> AppState:
    """Move the triage highlight by ``delta``, clamped, discarding a typed id."""
    if not state.triage_rows:
        return state
    target = max(0, min(state.triage_selected + delta, len(state.triage_rows) - 1))
    return replace(state, triage_selected=target, triage_input="")


def _parse_task_id(text: str) -> Optional[int]:
    """Return the positive int a triage input holds, or None if it is not one."""
    if not text.isdigit():
        return None
    value = int(text)
    return value if value > 0 else None


def assign_triage(deps: TuiDeps, state: AppState) -> AppState:
    """Attribute the selected series/event to the typed task id, then reload rows.

    Parses the typed keystrokes into a positive integer task id (a UI concern),
    then routes the write through the ``assign_event`` command, which owns the
    validated, atomic series write and is shared with MCP/CLI. Re-queries so the
    now-attributed row drops out of the queue. The confirmation names the series
    and the number of events updated; the events are immediately derivable and
    therefore billable.
    """
    if not state.triage_rows:
        return replace(state, status="nothing to triage")
    task_id = _parse_task_id(state.triage_input)
    if task_id is None:
        return replace(state, status="invalid task id — type a positive integer")
    row = state.triage_rows[state.triage_selected]
    result = deps.registry["assign_event"].execute(
        event_ids=list(row.event_ids), task_id=task_id
    )
    updated = result["updated"]
    rows = _load_triage_rows(deps, state.window)
    selected = min(state.triage_selected, max(0, len(rows) - 1))
    return replace(
        state,
        triage_rows=rows,
        triage_selected=selected,
        triage_input="",
        status=f"assigned {updated} events of series {row.display_key} to task {task_id}",
    )


def handle_triage_key(deps: TuiDeps, state: AppState, key: int) -> AppState:
    """Advance triage-mode state for one keypress (never quits the app).

    ``q``/ESC returns to the timeline; up/down move the highlight; ``s`` skips to
    the next row; digits build the task id; backspace edits it; Enter assigns.
    """
    if key in _QUIT_KEYS:
        return _exit_triage(state)
    if key == curses.KEY_UP:
        return _move_triage_selection(state, -1)
    if key == curses.KEY_DOWN or key == _SKIP_KEY:
        return _move_triage_selection(state, 1)
    if key in _BACKSPACE_KEYS:
        return replace(state, triage_input=state.triage_input[:-1])
    if key in _ENTER_KEYS:
        return assign_triage(deps, state)
    if ord("0") <= key <= ord("9"):
        return replace(state, triage_input=state.triage_input + chr(key))
    return state


def _member_events(
    store: Any, sessions: list[dict[str, Any]]
) -> dict[int, list[EventRecord]]:
    """Fetch each session's member events (with payload + external id) from state.

    The ``query_sessions`` render embeds only a thin event summary; the review
    surface needs the full :class:`EventRecord` — its ``external_id`` for the
    citation trail and its ``payload`` for the unvalidated-id flag — so the member
    events are re-read read-only from the store by their ids.
    """
    return {
        session["session_id"]: store.get_events_by_ids(
            [event["event_id"] for event in session.get("events", [])]
        )
        for session in sessions
    }


def _fetch_logged_hours(
    deps: TuiDeps, sessions: list[dict[str, Any]], window: DateWindow
) -> dict[tuple[str, str], float]:
    """Best-effort read of already-logged Odoo hours per task/day (#378 item 7).

    Degrades gracefully: any transport failure (offline, auth, no employee record)
    is swallowed so the review surface still renders, just without the
    already-logged badge. The read is strictly read-only (``search_read``). The
    RPC client is the driver's own injected one, not a command's private field.
    """
    task_ids = [session["task_id"] for session in sessions]
    try:
        return logged_hours_by_task_day(
            deps.client, task_ids, window.start_iso(), window.end_iso()
        )
    except Exception:  # noqa: BLE001 - best-effort badge; any failure = no badge
        return {}


def enter_review(deps: TuiDeps, state: AppState) -> AppState:
    """Open the review surface over the current window's derived sessions.

    Builds one decorated card per session (#378 items 7-9): fetches member events
    from the store for the citation trail and confidence class, computes pairwise
    cross-task overlaps, and best-effort reads the day's already-logged Odoo hours
    for the already-logged badge. Everything informs the reviewer; nothing trims
    or uploads.
    """
    store = deps.store
    events_by_session = _member_events(store, state.sessions)
    overlaps = compute_overlaps(state.sessions)
    logged = _fetch_logged_hours(deps, state.sessions, state.window)
    cards = build_review_cards(state.sessions, events_by_session, logged, overlaps)
    return replace(
        state,
        mode=_MODE_REVIEW,
        review_cards=cards,
        review_selected=0,
        review_expanded=False,
        status=f"review — {len(cards)} session(s)",
    )


def _exit_review(state: AppState) -> AppState:
    """Leave the review surface and return to the timeline view."""
    return replace(state, mode=_MODE_MAIN, review_expanded=False, status="")


def _move_review_selection(state: AppState, delta: int) -> AppState:
    """Move the review highlight by ``delta``, clamped, collapsing the pane."""
    if not state.review_cards:
        return state
    target = max(0, min(state.review_selected + delta, len(state.review_cards) - 1))
    return replace(state, review_selected=target, review_expanded=False)


def _toggle_evidence(state: AppState) -> AppState:
    """Toggle the selected card's evidence pane (no-op with no cards)."""
    if not state.review_cards:
        return state
    return replace(state, review_expanded=not state.review_expanded)


def handle_review_key(state: AppState, key: int) -> AppState:
    """Advance review-mode state for one keypress (never quits the app).

    ``q``/ESC returns to the timeline; up/down move the highlight (collapsing the
    open pane); ``e``/Enter toggles the selected card's evidence pane. The cards
    are read-only — no key here trims hours or uploads.
    """
    if key in _QUIT_KEYS:
        return _exit_review(state)
    if key == curses.KEY_UP:
        return _move_review_selection(state, -1)
    if key == curses.KEY_DOWN:
        return _move_review_selection(state, 1)
    if key == _EXPORT_MD_KEY or key in _ENTER_KEYS:
        return _toggle_evidence(state)
    return state


def handle_key(
    deps: TuiDeps,
    state: AppState,
    key: int,
    *,
    writer: Callable[[str, str], str],
) -> tuple[AppState, bool]:
    """Advance the app state for one keypress; return ``(state, should_quit)``.

    Pure w.r.t. the terminal: it only calls commands (through ``deps``) and the
    injected ``writer``. In triage mode every key is routed to the triage handler
    (which never quits the app). Otherwise the confirm gate takes precedence so an
    armed upload consumes the next key as its yes/no answer.
    """
    if state.mode == _MODE_TRIAGE:
        return handle_triage_key(deps, state, key), False
    if state.mode == _MODE_REVIEW:
        return handle_review_key(state, key), False
    if state.pending_upload:
        return (
            confirm_upload(state=state, deps=deps, confirmed=key in _CONFIRM_KEYS),
            False,
        )
    if key in _QUIT_KEYS:
        return state, True
    action = _KEY_ACTIONS.get(key)
    if action is not None:
        return move_window(deps, state, action), False
    if key == _EXPORT_MD_KEY:
        return do_export(state, deps, "markdown", writer), False
    if key == _EXPORT_CSV_KEY:
        return do_export(state, deps, "csv", writer), False
    if key == _UPLOAD_KEY:
        return request_upload(state), False
    if key == _RESYNC_KEY:
        return do_resync(deps, state), False
    if key == _TRIAGE_KEY:
        return enter_triage(deps, state), False
    if key == _REVIEW_KEY:
        return enter_review(deps, state), False
    return state, False


def _file_writer(content: str, name: str) -> str:  # pragma: no cover
    """Write ``content`` to ``name`` in the current directory; return the path."""
    from pathlib import Path

    path = Path.cwd() / name
    path.write_text(content)
    return str(path)


def _compose(state: AppState, width: int, height: int) -> Any:  # pragma: no cover
    """Compose the frame for ``state`` — triage, review, or the timeline."""
    if state.mode == _MODE_TRIAGE:
        return compose_triage_frame(
            state.triage_rows,
            state.triage_selected,
            state.triage_input,
            width,
            height,
        )
    if state.mode == _MODE_REVIEW:
        return compose_review_frame(
            state.review_cards,
            state.review_selected,
            state.review_expanded,
            width,
            height,
        )
    return compose_frame(
        state.sessions, state.window, width, height, empty_hint=state.empty_hint
    )


def _draw(stdscr: Any, state: AppState) -> None:  # pragma: no cover
    """Blit the composed frame for ``state`` onto the curses screen."""
    height, width = stdscr.getmaxyx()
    frame = _compose(state, width, max(height - 1, 0))
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


def _loop(stdscr: Any, deps: TuiDeps) -> None:  # pragma: no cover
    """Run the interactive render/read loop until the user quits."""
    curses.curs_set(0)
    stdscr.keypad(True)
    state = refresh(deps, AppState(window=default_window(), sessions=[]))
    while True:
        _draw(stdscr, state)
        key = stdscr.getch()
        if key == curses.KEY_RESIZE:
            continue
        state, should_quit = handle_key(deps, state, key, writer=_file_writer)
        if should_quit:
            break


def run(deps: TuiDeps) -> None:  # pragma: no cover
    """Start the curses TUI bound to ``deps`` and run until quit.

    ``Ctrl+C`` at the blocking ``getch`` surfaces as ``KeyboardInterrupt``; treat
    it as a normal quit. ``curses.wrapper`` already restores the terminal in its
    own ``finally``, so swallowing the interrupt just avoids a noisy traceback.
    """
    try:
        curses.wrapper(_loop, deps)
    except KeyboardInterrupt:
        pass  # Ctrl+C is a normal quit; the terminal is already restored.
