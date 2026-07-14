"""Single-owner module for all ``account.analytic.line`` manipulation.

This module is the **sole owner** of every ``account.analytic.line``
create / write in the SDK (issue #181). No command, TUI surface, or other
utility writes Odoo timesheets directly — they route through the two idempotent
operations here. Record deletion (``unlink``) is purposefully not implemented
anywhere in the SDK (see :func:`odoo_sdk.transport.errors.forbid_unlink`), so
this module never deletes a timesheet row.

The operations:

* :func:`ensure_anchor` — create (or **adopt** an existing) single anchor row
  for a task, keyed by the task and the ``[/] Work in progress`` marker so a
  second ``start_task`` reuses the first row instead of duplicating it
  (kills #177).
* :func:`reconcile` — upsert the real elapsed hours / description onto the
  anchor row. Idempotent: safe to re-run, it only ever writes the one anchor.

Both operations send **scalar** ids to Odoo (never a one-element list), which
preserves the #170/#176 fix: a batch ``create`` returns ``[id]`` (a list) that
breaks the SQLite bind and later timesheet writes, so a single-dict ``create``
is issued and any list result is unwrapped to an int at the source.
"""

from datetime import date, datetime, timezone
from typing import Any, Optional

from odoo_sdk.client import OdooClient
from odoo_sdk.state import EventRecord, LocalStateClient

# The marker name every anchor row carries. Reusing the marker is what lets a
# repeated ``ensure_anchor`` adopt the existing row rather than duplicate it.
ANCHOR_NAME = "[/] Work in progress"

# The name written onto an anchor when a stale orphaned run is aborted across
# DBs (#331). Distinct from ``ANCHOR_NAME`` so a closed-out orphan is never
# re-adopted by a later ``ensure_anchor``.
ABORTED_ANCHOR_NAME = "[/] aborted stale run"


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


def reconcile(
    client: OdooClient,
    state: LocalStateClient,
    task_id: int,
    description: str,
    elapsed_hours: float,
) -> Optional[int]:
    """Upsert the real hours / description onto a task's anchor timesheet row.

    Idempotent: it writes the single anchor row and nothing else, so re-running
    it (e.g. a re-triggered TUI/ETL upload) only overwrites the same row rather
    than accumulating duplicates. It is the TUI/ETL upload path — **not**
    ``stop_task`` — that calls this to write hours; ``stop_task`` only transitions
    the local session. The anchor id is resolved from the local session store;
    when no anchor is known the call is a no-op.

    :param client: The Odoo API client (the only writer of the timesheet row).
    :param state: The local state store the anchor id is resolved through.
    :param task_id: The ``project.task`` id whose anchor is reconciled.
    :param description: The final work summary to store on the row.
    :param elapsed_hours: The billable hours to store on the row.
    :return: The reconciled anchor id, or ``None`` when the task has no anchor.
    """
    timesheet_id = _resolve_anchor_id(client, state, task_id)
    if timesheet_id is None:
        return None
    client.execute(
        "account.analytic.line",
        "write",
        [timesheet_id],
        {"unit_amount": elapsed_hours, "name": description},
    )
    return timesheet_id


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


def _resolve_anchor_id(
    client: OdooClient, state: LocalStateClient, task_id: int
) -> Optional[int]:
    """Return the anchor id for a task from the run store, else from Odoo.

    Prefers the active run's ``timesheet_id`` (the anchor created at start);
    falls back to an Odoo lookup so a reconcile still lands when the local
    run has already been stopped or is otherwise unavailable.
    """
    run = state.get_active_run(task_id)
    if run is not None and run.timesheet_id is not None:
        return run.timesheet_id
    return _find_anchor(client, task_id)
