"""Single-owner module for all ``account.analytic.line`` manipulation.

This module is the **sole owner** of every ``account.analytic.line``
create / write in the SDK (issue #181). No command, TUI surface, or other
utility writes Odoo timesheets directly — they route through the idempotent
operations here. Record deletion (``unlink``) is purposefully not implemented
anywhere in the SDK (see :func:`odoo_sdk.transport.errors.forbid_unlink`), so
this module never deletes a timesheet row.

The operations:

* :func:`ensure_anchor` — create (or **adopt** an existing) single anchor row
  for a task, keyed by the task and the ``[/] Work in progress`` marker so a
  second ``start_task`` reuses the first row instead of duplicating it
  (kills #177).
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

from odoo_sdk.client import OdooClient
from odoo_sdk.state import EventRecord, LocalStateClient

from .odoo_helpers import get_employee_id

# The marker name every anchor row carries. Reusing the marker is what lets a
# repeated ``ensure_anchor`` adopt the existing row rather than duplicate it.
ANCHOR_NAME = "[/] Work in progress"

# The name written onto an anchor when a stale orphaned run is aborted across
# DBs (#331). Distinct from ``ANCHOR_NAME`` so a closed-out orphan is never
# re-adopted by a later ``ensure_anchor``.
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
    producer for the MCP tool surface (it builds the ``EventRecord`` directly so
    non-task-scoped tools can log with an empty task scope). This helper remains
    for other callers that record a single-task event.

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


def ensure_anchor(
    client: OdooClient,
    task_id: int,
    project_id: int,
    employee_id: int,
    today: date,
) -> int:
    """Return the single anchor timesheet id for a task, creating one if needed.

    Idempotent: when an unreconciled ``[/] Work in progress`` row already exists
    for the task it is **adopted** (its id returned) rather than creating a
    second one, so a repeated ``start_task`` never duplicates the anchor (#177).
    Otherwise a single placeholder row (0 hours) is created and its scalar id
    returned.

    :param client: The Odoo API client.
    :param task_id: Resolved ``project.task`` id the anchor belongs to.
    :param project_id: Resolved ``project.project`` id.
    :param employee_id: The ``hr.employee`` id logging the time.
    :param today: The anchor row's date.
    :return: The scalar ``account.analytic.line`` id of the anchor.
    """
    existing = _find_anchor(client, task_id)
    if existing is not None:
        return existing
    vals = {
        "name": ANCHOR_NAME,
        "unit_amount": 0.0,
        "project_id": project_id,
        "task_id": task_id,
        "date": today.isoformat(),
        "employee_id": employee_id,
    }
    return _scalar_id(client.execute("account.analytic.line", "create", vals))


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
    project = rows[0]["project_id"]
    return project[0] if isinstance(project, (list, tuple)) else project


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


# Marker written onto an orphaned upload row the sweep zeroes (#353). Distinct
# from every other anchor marker so a superseded row is unmistakable in Odoo and
# is never re-adopted by a later ``ensure_anchor``.
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
    2. **Adopt** — no mapping yet, but an unreconciled ``[/] Work in progress``
       anchor exists for the task (the 0-hour row ``start_task`` created); it is
       adopted (hours/description/date written) and the mapping recorded.
    3. **Create** — otherwise a fresh billed line is created (project resolved
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
    else:
        anchor = _find_anchor(client, task_id)
        if anchor is not None:
            timesheet_id = anchor
            _write_line(
                client,
                timesheet_id,
                {"unit_amount": hours, "name": description, "date": day.isoformat()},
            )
        else:
            timesheet_id = _create_session_line(
                client, state, task_id, description, hours, day
            )
    state.record_session_upload(
        session_key,
        timesheet_id,
        hours,
        task_id=str(task_id),
        started_at=started_at,
        ended_at=ended_at,
    )
    return timesheet_id


def _as_utc(ts: datetime) -> datetime:
    """Return ``ts`` as an aware UTC datetime (naive bounds are assumed UTC)."""
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)


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
        lo, hi = _as_utc(window_lo), _as_utc(window_hi)
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
