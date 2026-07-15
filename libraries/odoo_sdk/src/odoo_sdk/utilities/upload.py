"""The shared derived-session upload loop (issue #354).

This is the single upload code path shared by every surface: the ``odoo-tui``
``u`` key and the headless ``odoo-sdk upload`` subcommand both derive sessions
the same way (the ``query_sessions`` command) and feed those session dicts into
:func:`upload_sessions` here, so a non-interactive invocation bills exactly the
rows the TUI would. It lives in ``utilities`` — not in a command — because it is
shared logic between two interaction surfaces (the SDK's stated home for such
code) and the built-in command surface is pinned 1:1 to the explicit MCP tool
surface, which deliberately does not expose an upload tool.

The loop is: **reconcile each session, then orphan-sweep once**. Sessions are
reconciled through :func:`odoo_sdk.utilities.timesheet.reconcile_session`, the
sole ``account.analytic.line`` hours-writer for the derived-upload path, so a
re-run never double-bills (each session's stable ``session_key`` maps to one
row). Sessions lacking a numeric task id carry no Odoo task to bill and are
skipped. After billing, the stale-mapping sweep
(:func:`odoo_sdk.utilities.timesheet.sweep_orphaned_uploads`, #353) diffs the
upload ledger against the just-derived key set for the window and zeroes /
retires any mapping that no longer derives, so merged-away sessions are not
double-counted. Because the sweep runs inside this shared path, both entry
points get it. In ``dry_run`` mode the same sessions are selected and
summarised but no Odoo write (bill or sweep) is issued.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional

from odoo_sdk.client import OdooClient
from odoo_sdk.state import LocalConfig, LocalStateClient

from .timesheet import reconcile_session, sweep_orphaned_uploads


def _round_to_step(value: float, step: float) -> float:
    """Round ``value`` to the nearest multiple of ``step`` (half-up).

    Half-up (not banker's) rounding is used so a session landing exactly on a
    half-step always rounds toward the larger multiple, matching how billing is
    read by a human. A ``step`` of ``0`` (or negative) disables rounding and
    returns ``value`` unchanged. :class:`~decimal.Decimal` arithmetic on the
    string forms avoids binary-float drift (e.g. ``1.87 / 0.05`` landing at
    ``37.399999`` and rounding down to ``1.85`` for the wrong reason).
    """
    if step <= 0:
        return value
    quotient = (Decimal(str(value)) / Decimal(str(step))).to_integral_value(
        rounding=ROUND_HALF_UP
    )
    return float(quotient * Decimal(str(step)))


def _billable_hours(raw_hours: float, minimum: float, step: float) -> float:
    """Apply the configurable billing policy to one session's wall-clock span.

    The single choke point for issue #355: a derived session bills its raw
    wall-clock span, but that silently under-bills — a single-event session
    (``MIN == MAX`` timestamp) spans zero hours and a 30-second session rounds
    toward nothing. The span is first rounded to the nearest ``step`` multiple
    (half-up; ``step == 0`` disables rounding), then raised to ``minimum`` so a
    below-minimum session is floored *up* to the minimum (never dropped) and the
    rounded result can never dip below the floor. Both the TUI ``u`` key and
    ``odoo-sdk upload`` reach this through :func:`upload_sessions`.

    :param raw_hours: The session's raw wall-clock span in hours.
    :param minimum: The per-session floor in hours (``min_session_hours``).
    :param step: The rounding multiple in hours (``round_session_hours``).
    :return: The hours to bill, ``>= minimum``.
    """
    return max(_round_to_step(raw_hours, step), minimum)


def _numeric_task_id(value: Any) -> Optional[int]:
    """Return ``value`` as an int when it is a numeric task id, else None.

    A derived session whose ``task_id`` is non-numeric (e.g. the repo-less
    sentinel or an unresolved label) has no Odoo ``project.task`` to bill, so
    the caller skips it rather than fabricating a target.
    """
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def range_bounds(
    start_date: Optional[str], end_date: Optional[str]
) -> tuple[datetime, datetime]:
    """Resolve inclusive ISO date strings into the ``[lo, hi)`` window bounds.

    Matches the ``query_sessions`` command's inclusive-date semantics (and the
    TUI's window bounds): ``lo`` is midnight of the start day and ``hi`` is
    midnight of the day after the end, so the whole end day is covered. An
    omitted bound defaults to the widest representable range, letting a caller
    upload "everything". The orphan sweep is scoped to these same bounds so it
    only retires mappings belonging to the queried window.
    """
    start = date.fromisoformat(start_date) if start_date else None
    end = date.fromisoformat(end_date) if end_date else None
    lo = datetime.combine(start, time.min) if start else datetime.min
    if end is None:
        return lo, datetime.max
    return lo, datetime.combine(end + timedelta(days=1), time.min)


def _upload_one(
    client: OdooClient,
    state: LocalStateClient,
    task_id: int,
    session: dict[str, Any],
    dry_run: bool,
    minimum: float,
    step: float,
) -> dict[str, Any]:
    """Bill one derived session and return its summary row.

    Delegation only: the session's identity (numeric ``task_id``, stable
    ``session_key``, ``duration_secs`` and its ``started_at``/``ended_at``
    bounds) is passed to :func:`reconcile_session`, which resolves and rewrites
    the single row the session maps to and records the window bounds the orphan
    sweep keys on. The raw wall-clock span is passed through the billing policy
    (:func:`_billable_hours`, issue #355) first, so the minimum and rounding
    apply to whatever is actually billed — and, because the same billed hours
    populate the summary row, a dry-run preview shows exactly what a real run
    would write. Idempotent per session key. On a dry run nothing is written and
    the row's ``timesheet_id`` is None.
    """
    raw_hours = float(session.get("duration_secs", 0)) / 3600
    hours = _billable_hours(raw_hours, minimum, step)
    key = session.get("session_key", "")
    timesheet_id: Optional[int] = None
    if not dry_run:
        timesheet_id = reconcile_session(
            client,
            state,
            task_id,
            key,
            f"[/] session {key}",
            hours,
            datetime.fromisoformat(session["started_at"]),
            datetime.fromisoformat(session["ended_at"]),
        )
    return {
        "task_id": task_id,
        "session_key": key,
        "hours": hours,
        "timesheet_id": timesheet_id,
    }


def _sweep(
    client: OdooClient,
    state: LocalStateClient,
    sessions: list[dict[str, Any]],
    window_lo: datetime,
    window_hi: datetime,
) -> int:
    """Run the window-scoped orphan sweep after an upload (#353).

    Diffs the upload ledger against the just-derived key set: a
    previously-uploaded session that no longer derives — its events merged into
    an adjacent session by a backfilled event — has its Odoo row zeroed and its
    mapping retired, so the merged-away hours are not double-counted.

    :return: The number of mappings retired.
    """
    return sweep_orphaned_uploads(
        client,
        state,
        derived_keys={session.get("session_key", "") for session in sessions},
        derived_task_ids={str(session.get("task_id")) for session in sessions},
        window_lo=window_lo,
        window_hi=window_hi,
    )


def upload_sessions(
    client: OdooClient,
    state: LocalStateClient,
    sessions: list[dict[str, Any]],
    *,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    dry_run: bool = False,
    config: Optional[LocalConfig] = None,
) -> dict[str, Any]:
    """Bill each derived session, then sweep orphaned upload mappings.

    The single shared upload path (#354): every billable session (numeric task
    id) is reconciled through the sole hours-writer, then the stale-mapping
    sweep runs once, scoped to the same inclusive date range the sessions were
    derived over. Callers pass the range they queried (``start_date`` /
    ``end_date``) rather than precomputed bounds — the bounds are resolved here
    via :func:`range_bounds`, so the query window and the sweep window cannot
    drift apart. When ``dry_run`` is set the billable set is computed and
    summarised but no Odoo write is issued — neither the per-session reconcile
    nor the sweep — so callers can preview exactly what a real run would bill.

    :param client: The Odoo API client (the only writer of the timesheet rows).
    :param state: The local state client the upload ledger is kept in.
    :param sessions: ``query_sessions`` result dicts to bill.
    :param start_date: Inclusive ISO start date (``YYYY-MM-DD``) of the range
        the sessions were derived over, or None for unbounded.
    :param end_date: Inclusive ISO end date, or None for unbounded.
    :param dry_run: When True, select and summarise but write nothing to Odoo.
    :param config: Resolved SDK settings supplying the per-session billing
        policy (``min_session_hours`` / ``round_session_hours``, issue #355).
        When omitted it is loaded via :meth:`LocalConfig.load` so both entry
        points (the TUI ``u`` key and ``odoo-sdk upload``) pick up the file and
        environment overrides automatically.
    :return: A summary dict with ``uploaded`` (billable session count),
        ``skipped`` (non-numeric-task sessions), ``retired`` (orphan mappings
        swept), ``dry_run``, and ``rows`` (one ``{task_id, session_key, hours,
        timesheet_id}`` per billable session; ``hours`` is the policy-adjusted
        billed hours; ``timesheet_id`` is None on a dry run).
    """
    config = config or LocalConfig.load()
    minimum = config.min_session_hours
    step = config.round_session_hours
    rows: list[dict[str, Any]] = []
    skipped = 0
    for session in sessions:
        task_id = _numeric_task_id(session.get("task_id"))
        if task_id is None:
            skipped += 1
            continue
        rows.append(
            _upload_one(client, state, task_id, session, dry_run, minimum, step)
        )
    retired = 0
    if not dry_run:
        window_lo, window_hi = range_bounds(start_date, end_date)
        retired = _sweep(client, state, sessions, window_lo, window_hi)
    return {
        "uploaded": len(rows),
        "skipped": skipped,
        "retired": retired,
        "dry_run": dry_run,
        "rows": rows,
    }
