"""Single-owner module for all ``account.analytic.line`` manipulation.

This module is the **sole owner** of every ``account.analytic.line``
create / write in the SDK (issue #181). No command, TUI surface, or other
utility writes Odoo timesheets directly — they route through the idempotent
operations here. Record deletion (``unlink``) is purposefully not implemented
anywhere in the SDK (see :func:`odoo_sdk.transport.errors.forbid_unlink`), so
this module never deletes a timesheet row.

The operations:

* :func:`reconcile_session` — upsert one derived session's real hours /
  description onto the single ``account.analytic.line`` it maps to (by its
  stable ``session_key``). Idempotent: safe to re-run, it only ever rewrites
  the one row a session is mapped to, so an upload never double-bills. This is
  the sole hours-writer for the derived-session upload path (#330/#354).

Both operations send **scalar** ids to Odoo (never a one-element list), which
preserves the #170/#176 fix: a batch ``create`` returns ``[id]`` (a list) that
breaks the SQLite bind and later timesheet writes, so a single-dict ``create``
is issued and any list result is unwrapped to an int at the source.
"""

from datetime import date, datetime, timezone
from typing import Any, Optional

from odoo_sdk._utils import as_utc
from odoo_sdk.client import OdooClient
from odoo_sdk.state import EventRecord, LocalStateClient
from odoo_sdk.utilities.odoo_helpers import get_employee_id, m2o_id

# The marker a legacy anchor row carries. The FSM no longer creates anchors
# (#325), but :func:`_find_anchor` still keys on this marker to adopt any
# pre-#325 anchor into the upload's reconcile path rather than orphan it.
ANCHOR_NAME = "[/] Work in progress"

# The name written onto an anchor when a stale orphaned run is aborted across
# DBs (#331). Distinct from ``ANCHOR_NAME`` so a closed-out orphan is never
# re-adopted by a later reconcile via :func:`_find_anchor`.
ABORTED_ANCHOR_NAME = "[/] aborted stale run"


def resolve_employee_id(client: OdooClient, state: LocalStateClient) -> int:
    """Return the ``hr.employee`` id for the authenticated user, caching it.

    Moved out of ``start_task`` so the timesheet module (the sole
    ``account.analytic.line`` writer) can create a fresh session line without
    reaching back into a command. The id is cached in the local settings on first
    fetch so subsequent calls avoid the Odoo round-trip.
    """
    cached = state.get_setting("employee_id")
    if cached is not None:
        return int(cached)
    employee_id = get_employee_id(client, client.uid)
    state.set_setting("employee_id", str(employee_id))
    return employee_id


def emit_agent_event(
    state: LocalStateClient,
    task_id: int,
    subject: str,
    *,
    payload: Optional[dict] = None,
) -> EventRecord:
    """Record one ``EventType.AGENT`` event for a task-scoped tool call (#180).

    The FSM tools do measurable work but historically emitted no sessionization
    events, so agent activity never reached ``events -> sessions -> billing``.
    This helper is the single-task-scoped producer that logs an ``agent`` event
    the persistence adapter maps to ``EventType.AGENT`` and the ``development``
    session-kind strategy then derives session windows from.

    As of #326, MCP tool dispatch events are **not** emitted through here: the
    generic ``_event_emitting`` wrapper in :mod:`odoo_sdk.mcp.server` is the sole
    producer for the MCP tool surface, and since #427 it routes through
    :class:`~odoo_sdk.commands.log_event.LogEventCommand` rather than building
    the ``EventRecord`` itself, so non-task-scoped tools can log with an empty
    task scope. This helper has no production callers left; it is retained as
    the supported single-task-scoped entry point for future callers.

    Agent events are repo-less; the sessionizer groups them under its reserved
    repo-less sentinel, so no repo is threaded here.

    :param state: The local state store the event timeseries is appended to.
    :param task_id: The ``project.task`` id the work attributes to.
    :param subject: A short human-readable summary (tool name + detail).
    :param payload: Optional structured detail stored alongside the event.
    :return: The persisted event record.
    """
    return state.add_event(
        EventRecord(
            id=None,
            source="agent",
            timestamp=datetime.now(timezone.utc),
            task_ids=[str(task_id)],
            repo="",
            subject=subject,
            payload=payload,
        )
    )


def _scalar_id(result: Any) -> int:
    """Unwrap a possibly list-wrapped Odoo ``create`` result to a scalar int.

    Odoo's ORM ``create`` answers a *batch* (list-of-dicts) call with ``[id]``
    and a *single* (dict) call with a scalar ``id``. A list id breaks the
    SQLite bind in ``create_run`` and, one hop later, the reconcile timesheet
    write on the upload path (``TypeError: unhashable type: list`` — #176).
    Unwrapping here guarantees the stored id is always a scalar int.
    """
    if isinstance(result, (list, tuple)):
        return int(result[0])
    return int(result)


def _find_anchor(client: OdooClient, task_id: int) -> Optional[int]:
    """Return the id of the task's existing ``[/] Work in progress`` anchor, or None.

    The lookup keys on ``task_id`` plus the anchor marker so only a genuine
    unreconciled anchor is adopted; a reconciled row (real description) is left
    untouched. The lowest id wins so repeated adoption is deterministic.

    As of #325 the FSM no longer *creates* anchors, so this only ever matches a
    **legacy** row left over from a pre-#325 ``start_task`` — kept so the upload
    path adopts and closes it out (rather than orphaning it) during the upgrade
    window; :func:`close_anchor` likewise retires stale legacy anchors on abort.
    """
    rows = client.execute(
        "account.analytic.line",
        "search_read",
        [("task_id", "=", task_id), ("name", "=", ANCHOR_NAME)],
        fields=["id"],
        order="id asc",
        limit=1,
    )
    return rows[0]["id"] if rows else None


def close_anchor(client: OdooClient, timesheet_id: Optional[int]) -> bool:
    """Close out an orphaned anchor timesheet row after a stale-run abort (#331).

    Reads the ``account.analytic.line`` row and rewrites it to
    ``{"name": ABORTED_ANCHOR_NAME, "unit_amount": 0.0}`` **only** when its name
    is still the unreconciled :data:`ANCHOR_NAME` marker — a row a human has
    since edited (any other name) is left untouched, never clobbered. The SDK
    never deletes records, so the row is retired in place rather than removed.

    :param client: The Odoo API client (the only writer of the timesheet row).
    :param timesheet_id: The anchor row id to close, or ``None`` for a run that
        never had one.
    :return: ``True`` when the row was closed; ``False`` when there is no id, no
        such row, or the row was already edited/closed.
    """
    if timesheet_id is None:
        return False
    rows = client.execute(
        "account.analytic.line", "read", [timesheet_id], fields=["name"]
    )
    if not rows or rows[0].get("name") != ANCHOR_NAME:
        return False
    client.execute(
        "account.analytic.line",
        "write",
        [timesheet_id],
        {"name": ABORTED_ANCHOR_NAME, "unit_amount": 0.0},
    )
    return True


def _write_line(client: OdooClient, timesheet_id: int, vals: dict) -> None:
    """Write ``vals`` onto one ``account.analytic.line`` row (scalar-id safe)."""
    client.execute("account.analytic.line", "write", [timesheet_id], vals)


def _project_id_for(client: OdooClient, task_id: int) -> int:
    """Return the ``project.project`` id owning a ``project.task``."""
    rows = client.execute("project.task", "read", [task_id], fields=["project_id"])
    return m2o_id(rows[0]["project_id"])


def _create_session_line(
    client: OdooClient,
    state: LocalStateClient,
    task_id: int,
    description: str,
    hours: float,
    day: date,
) -> int:
    """Create a fresh billed ``account.analytic.line`` for a derived session."""
    vals = {
        "name": description,
        "unit_amount": hours,
        "project_id": _project_id_for(client, task_id),
        "task_id": task_id,
        "date": day.isoformat(),
        "employee_id": resolve_employee_id(client, state),
    }
    return _scalar_id(client.execute("account.analytic.line", "create", vals))


def _record_upload(
    state: LocalStateClient,
    session_key: str,
    timesheet_id: int,
    hours: float,
    task_id: int,
    started_at: datetime,
    ended_at: datetime,
) -> None:
    """Upsert the idempotency-ledger mapping for one reconciled session.

    Ties the session's stable ``session_key`` to the single
    ``account.analytic.line`` id it was reconciled onto (plus the hours,
    ``task_id`` and window bounds the orphan sweep keys on). Recording is
    idempotent, so it is safe to write before the Odoo row is touched — the
    adopt path relies on this ordering to stay double-bill-safe (#582).
    """
    state.record_session_upload(
        session_key,
        timesheet_id,
        hours,
        task_id=str(task_id),
        started_at=started_at,
        ended_at=ended_at,
    )


# Marker written onto an orphaned upload row the sweep zeroes (#353). Distinct
# from every other anchor marker so a superseded row is unmistakable in Odoo and
# is never re-adopted by a later reconcile via :func:`_find_anchor`.
ORPHANED_UPLOAD_NAME = "[x] session superseded (merged into another session)"


def reconcile_session(
    client: OdooClient,
    state: LocalStateClient,
    task_id: int,
    session_key: str,
    description: str,
    hours: float,
    started_at: datetime,
    ended_at: datetime,
) -> int:
    """Idempotently write one derived session's hours onto a single timesheet row.

    This is the sole hours-writer for the SQL-derived upload path (#330). It
    resolves the one ``account.analytic.line`` a session maps to and writes the
    hours/description onto it, in three precedence tiers:

    1. **Mapped** — a prior upload for ``session_key`` is recorded; its timesheet
       row is rewritten (hours/description) and the mapping's hours updated.
    2. **Adopt** — no mapping yet, but a legacy unreconciled ``[/] Work in
       progress`` anchor exists for the task (a 0-hour row a pre-#325
       ``start_task`` created — the FSM no longer makes these); it is adopted
       (hours/description/date written) and the mapping recorded, so upgrade-era
       anchors are closed out rather than orphaned.
    3. **Create** — otherwise (the norm now) a fresh billed line is created (project resolved
       from the task, employee via :func:`resolve_employee_id`) and mapped.

    Re-running with the same ``session_key`` always rewrites the same row, so the
    upload is idempotent and never double-bills. The session's ``started_at`` /
    ``ended_at`` bounds are recorded on the mapping so the orphan sweep
    (:func:`sweep_orphaned_uploads`) can window-scope it (#353).

    :return: The ``account.analytic.line`` id the session was reconciled onto.
    """
    day = started_at.date()
    mapped = state.get_session_upload(session_key)
    if mapped is not None:
        timesheet_id = mapped["timesheet_id"]
        _write_line(client, timesheet_id, {"unit_amount": hours, "name": description})
        _record_upload(
            state, session_key, timesheet_id, hours, task_id, started_at, ended_at
        )
        return timesheet_id
    anchor = _find_anchor(client, task_id)
    if anchor is not None:
        # #582 idempotency: record the ledger mapping BEFORE renaming the adopted
        # anchor. The write rewrites the anchor's name away from ``ANCHOR_NAME``,
        # so :func:`_find_anchor` can no longer re-adopt it. Were the mapping
        # recorded only after the write (the old order), a crash landing between
        # the two steps would leave a retry with neither a mapping nor an
        # adoptable anchor — and the create branch below would bill a SECOND
        # line (double-bill). Recording first makes any retry re-find the row
        # through the mapped branch and rewrite it in place.
        _record_upload(
            state, session_key, anchor, hours, task_id, started_at, ended_at
        )
        _write_line(
            client,
            anchor,
            {"unit_amount": hours, "name": description, "date": day.isoformat()},
        )
        return anchor
    timesheet_id = _create_session_line(
        client, state, task_id, description, hours, day
    )
    _record_upload(
        state, session_key, timesheet_id, hours, task_id, started_at, ended_at
    )
    return timesheet_id


def _mapping_is_window_orphan(
    entry: dict,
    derived_task_ids: set[str],
    window_lo: datetime,
    window_hi: datetime,
) -> bool:
    """Return True when a stale ledger ``entry`` should be retired for this window.

    An entry not in the currently-derived key set is only an orphan when it
    belongs to *this* window — otherwise a later window's upload would wrongly
    zero a session it simply did not re-derive (it was out of range).

    * **New-format rows** (``started_at``/``ended_at`` present): orphaned when the
      recorded session window overlaps the queried ``[window_lo, window_hi]``.
    * **Legacy rows** (NULL bounds — written before #353, and/or a pre-#352
      ``task|repo|id`` key that can never re-derive under the task-only format):
      retired deliberately when the key's task prefix appears in this window's
      derived set, so they are cleaned up rather than lingering as double-counts.
    """
    started, ended = entry.get("started_at"), entry.get("ended_at")
    if started is not None and ended is not None:
        lo, hi = as_utc(window_lo), as_utc(window_hi)
        return (
            datetime.fromisoformat(ended) >= lo
            and datetime.fromisoformat(started) <= hi
        )
    return entry["session_key"].split("|")[0] in derived_task_ids


def sweep_orphaned_uploads(
    client: OdooClient,
    state: LocalStateClient,
    *,
    derived_keys: set[str],
    derived_task_ids: set[str],
    window_lo: datetime,
    window_hi: datetime,
) -> int:
    """Zero and retire upload mappings that no longer derive for this window (#353).

    Run once per upload invocation. A mapping whose key is still in
    ``derived_keys`` is left untouched. Any other mapping that belongs to this
    window (see :func:`_mapping_is_window_orphan`) has been merged into an
    adjacent session — a backfilled event bridged the gap — so its Odoo row would
    keep double-counted hours forever. The SDK never unlinks, so the row is zeroed
    (``unit_amount=0`` + :data:`ORPHANED_UPLOAD_NAME`) and the mapping deleted.

    :return: The number of mappings retired.
    """
    retired = 0
    for entry in state.list_session_uploads():
        if entry["session_key"] in derived_keys:
            continue
        if not _mapping_is_window_orphan(
            entry, derived_task_ids, window_lo, window_hi
        ):
            continue
        _write_line(
            client,
            entry["timesheet_id"],
            {"unit_amount": 0.0, "name": ORPHANED_UPLOAD_NAME},
        )
        state.delete_session_upload(entry["session_key"])
        retired += 1
    return retired


def update_timesheet(
    client: OdooClient,
    timesheet_id: int,
    unit_amount: float,
    description: str,
) -> None:
    """Update one timesheet row's final elapsed hours (``unit_amount``) and name."""
    _write_line(client, timesheet_id, {"unit_amount": unit_amount, "name": description})


def merge_timesheets(
    client: OdooClient, primary_id: int, ids_to_merge: list[int]
) -> None:
    """Sum unit_amount and join descriptions onto the primary timesheet row.

    Record deletion via ``unlink`` is purposefully not implemented in this SDK
    (irrecoverable data loss risk), so the merged-in rows are **kept in place**
    rather than deleted. To stop them double-counting their hours after the sum
    is written onto the primary row, their ``unit_amount`` is zeroed with a
    single ``write`` and their ``name`` is prefixed with ``[merged]`` for
    traceability. The rows remain readable but contribute 0 hours.

    :param client: Connected Odoo client.
    :param primary_id: The surviving ``account.analytic.line`` id that keeps the
        summed hours and joined description.
    :param ids_to_merge: The rows folded into ``primary_id`` and then zeroed.
    """
    all_ids = [primary_id] + ids_to_merge
    # #166 shape: a flat id list positionally, ``fields`` as a keyword. Wrapping
    # the ids or trailing a ``{"fields": [...]}`` dict makes Odoo read the dict
    # as the positional ``fields`` argument and raise "Invalid field 'fields'".
    records = client.execute(
        "account.analytic.line",
        "read",
        all_ids,
        fields=["id", "unit_amount", "name"],
    )
    total_hours = sum(r["unit_amount"] for r in records)
    descriptions = list(
        dict.fromkeys(r["name"] for r in records if r["name"] != ANCHOR_NAME)
    )
    merged_desc = " | ".join(descriptions) if descriptions else ANCHOR_NAME
    update_timesheet(client, primary_id, total_hours, merged_desc)
    if ids_to_merge:
        # Zero the merged-in rows so they no longer double-count their hours,
        # keeping them in place because ``unlink`` is forbidden system-wide.
        client.execute(
            "account.analytic.line",
            "write",
            ids_to_merge,
            {"unit_amount": 0.0, "name": "[merged] " + merged_desc},
        )
