"""Pure application state and transitions for the Textual TUI.

This module owns the terminal-agnostic half of the TUI: the injected
:class:`TuiDeps` bundle, the serializable :class:`AppState`, and every state
transition (refresh, window moves, export, the upload confirm gate, resync,
triage, and review). It holds no business logic — session detection is the
``query_sessions`` command's job, export reuses the #105 renderers, upload
delegates to the shared billing loop behind a confirm gate, and the triage
write delegates to the ``assign_event`` command.

Its dependencies — the RPC client, the local state store, and the resolved
config, plus the command registry it composes — are injected once at
construction as a :class:`TuiDeps` bundle (``tui/__main__`` has all of them in
hand). The transitions never harvest them off command instances (no reaching
into a command's private ``._client`` or its ``.state`` / ``.config``); every
state mutation goes through a command, so MCP and CLI can share the same
operations.

The genuinely terminal-bound half — the Textual :class:`~textual.app.App`, its
screens, and their key bindings — lives in :mod:`~odoo_sdk.tui.textual_app` and
calls back into these pure transitions, so everything here is tested without a
terminal.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Any, Callable, Optional

from odoo_sdk.commands import Registry
from odoo_sdk.commands.protocols import RpcClient
from odoo_sdk.state import EventRecord, LocalConfig, LocalStateClient
from odoo_sdk.transport.errors import OdooError
from odoo_sdk.utilities.logged_lines import logged_hours_by_task_day
from odoo_sdk.billing.upload import range_bounds, upload_sessions

from .evidence import ReviewCard, build_review_cards, compute_overlaps
from .export import export_csv, export_markdown
from .triage import TriageRow, build_triage_rows
from .window import DateWindow, apply_action

# Keys (Textual key names) that answer the upload confirm gate affirmatively.
_CONFIRM_KEYS = ("y", "Y")

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
    task runs are on record overall (terminal ``CLOSED`` runs are excluded, #504),
    and the session gap the deriver uses.
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
    """Resolve the confirm gate: on ``confirmed`` run the upload, else cancel.

    The upload is fully guarded (#576): a server fault that escapes the loop
    (e.g. the sweep faulting, or the connection dropping) is caught and rendered
    on the status line rather than propagating out of the driver and killing the
    app. Per-session faults never even reach here — the shared loop isolates them
    (#582) into ``failed`` rows, which are summarised in the status alongside the
    billed count so a partial upload is visible instead of silent.
    """
    if not confirmed:
        return replace(state, pending_upload=False, status="upload cancelled")
    try:
        result = _run_upload(deps, state.sessions, state.window)
    except OdooError as exc:
        return replace(
            state, pending_upload=False, status=f"upload failed: {exc}"
        )
    return replace(state, pending_upload=False, status=_upload_status(result))


def _run_upload(
    deps: TuiDeps, sessions: list[dict[str, Any]], window: DateWindow
) -> dict[str, Any]:
    """Bill the derived sessions through the shared upload loop (#354).

    The ``u`` key and the headless ``odoo-sdk upload`` subcommand share the one
    :func:`~odoo_sdk.billing.upload.upload_sessions` path: idempotent per
    ``session_key`` (a re-run never double-bills) plus a window-scoped orphan
    sweep (#353) scoped to the inclusive dates forwarded here. Returns the full
    summary dict so the driver can surface partial failures (#582) in-app.
    """
    return upload_sessions(
        deps.client,
        deps.store,
        sessions,
        start_date=window.start_iso(),
        end_date=window.end_iso(),
    )


def _upload_sessions(
    deps: TuiDeps, sessions: list[dict[str, Any]], window: DateWindow
) -> tuple[int, int]:
    """Return the shared upload loop's ``(uploaded, retired)`` counts (#354).

    The thin two-count view of :func:`_run_upload`, kept as the surface the
    TUI/CLI billing-parity tests assert against (both entry points bill the
    identical rows through the one loop).
    """
    result = _run_upload(deps, sessions, window)
    return int(result["uploaded"]), int(result["retired"])


def _upload_status(result: dict[str, Any]) -> str:
    """Render the post-upload status line: billed, retired, and failed counts.

    Failures (#582) are summarised with the first fault's reason so a partial
    upload is never silent — the user sees how many sessions billed, how many
    orphan mappings were retired, and how many sessions faulted (and why).
    """
    parts = [f"uploaded {int(result['uploaded'])} session(s)"]
    retired = int(result.get("retired", 0))
    if retired:
        parts.append(f"retired {retired} orphaned upload(s)")
    failed = result.get("failed", [])
    if failed:
        parts.append(_failed_summary(failed))
    return ", ".join(parts)


def _failed_summary(failed: list[dict[str, Any]]) -> str:
    """Summarise the failed-session rows for the one-line status (#582/#576)."""
    reason = failed[0].get("error") or "unknown error"
    if len(failed) == 1:
        return f"1 failed ({reason})"
    return f"{len(failed)} failed (first: {reason})"


def _source_summary(outcome: dict[str, Any]) -> str:
    """Render one puller's outcome: an inserted count, error, or skip reason.

    ``error`` (tooling entirely unusable, #652) renders distinctly from an
    optional source's ``skipped``; a success surfaces partial degradation
    (repos whose log failed) and how many newly stored review events resolved
    no task id (#653), so neither is ever silent.
    """
    if "error" in outcome:
        return f"error ({outcome['error']})"
    if "skipped" in outcome:
        return f"skipped ({outcome['skipped']})"
    summary = f"+{outcome['inserted']}"
    failed = outcome.get("failed_repos")
    if failed:
        summary += f" ({failed} of {outcome.get('repos', '?')} repos failed)"
    unattributed = outcome.get("unattributed_reviews") or []
    if unattributed:
        summary += f" ({len(unattributed)} review(s) without task id)"
    return summary


def _resync_status(result: dict[str, Any]) -> str:
    """Render the resync status line: per-source inserted counts / skip reasons."""
    if not result:
        return "resync — nothing to do"
    parts = [f"{source}: {_source_summary(outcome)}" for source, outcome in result.items()]
    return "resync — " + ", ".join(parts)


def do_resync(deps: TuiDeps, state: AppState) -> AppState:
    """Run the manual resync, re-query the window, and report per-source counts.

    Reconciles events directory-agnostically (#652) — git commits from every
    repo under the launch directory, the user's account-wide GitHub
    PRs/reviews/comments, and Odoo chatter — into local state, then refreshes
    so any newly derivable sessions appear immediately, and surfaces each
    source's inserted count (or its error/skip reason) on the status line.
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


def exit_triage(state: AppState) -> AppState:
    """Leave the triage queue and return to the timeline view."""
    return replace(state, mode=_MODE_MAIN, triage_input="", status="")


def move_triage_selection(state: AppState, delta: int) -> AppState:
    """Move the triage highlight by ``delta``, clamped, discarding a typed id."""
    if not state.triage_rows:
        return state
    target = max(0, min(state.triage_selected + delta, len(state.triage_rows) - 1))
    return replace(state, triage_selected=target, triage_input="")


def type_triage_digit(state: AppState, digit: str) -> AppState:
    """Append one typed digit to the triage task-id input."""
    return replace(state, triage_input=state.triage_input + digit)


def erase_triage_digit(state: AppState) -> AppState:
    """Erase the last typed digit of the triage task-id input."""
    return replace(state, triage_input=state.triage_input[:-1])


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


def exit_review(state: AppState) -> AppState:
    """Leave the review surface and return to the timeline view."""
    return replace(state, mode=_MODE_MAIN, review_expanded=False, status="")


def move_review_selection(state: AppState, delta: int) -> AppState:
    """Move the review highlight by ``delta``, clamped, collapsing the pane."""
    if not state.review_cards:
        return state
    target = max(0, min(state.review_selected + delta, len(state.review_cards) - 1))
    return replace(state, review_selected=target, review_expanded=False)


def toggle_evidence(state: AppState) -> AppState:
    """Toggle the selected card's evidence pane (no-op with no cards)."""
    if not state.review_cards:
        return state
    return replace(state, review_expanded=not state.review_expanded)


def _file_writer(content: str, name: str) -> str:  # pragma: no cover
    """Write ``content`` to ``name`` in the current directory; return the path."""
    from pathlib import Path

    path = Path.cwd() / name
    path.write_text(content)
    return str(path)


def run(deps: TuiDeps) -> None:
    """Start the Textual TUI bound to ``deps`` and run until quit.

    ``Ctrl+C`` at the input loop surfaces as ``KeyboardInterrupt``; treat it as a
    normal quit. Textual already restores the terminal on shutdown, so swallowing
    the interrupt just avoids a noisy traceback.
    """
    from .textual_app import OdooTuiApp

    try:
        OdooTuiApp(deps).run()
    except KeyboardInterrupt:
        pass  # Ctrl+C is a normal quit; the terminal is already restored.
