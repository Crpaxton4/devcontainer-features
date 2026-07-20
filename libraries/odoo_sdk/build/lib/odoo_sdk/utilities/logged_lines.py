"""Read-only fetch of already-logged timesheet hours per task and day (#378 item 7).

The derived-session review surface needs to warn the reviewer when a day/task it
proposes as *new* time was already logged by hand in Odoo. ``session_uploads``
records only the SDK's own prior uploads, so manually-entered
``account.analytic.line`` rows are otherwise invisible and a derived session for
a day the user already logged reads as new, uploadable time.

This queries those rows strictly read-only (one ``search_read``; nothing is ever
written) and aggregates them by ``(task_id, day)`` so the TUI can badge each
session card with "already logged N h on this task today". It is meant to be
called best-effort: the caller swallows transport errors so an offline Odoo
simply yields no badge and the review surface still works.
"""

from __future__ import annotations

from typing import Any, Sequence

from odoo_sdk.client import OdooClient

from .odoo_helpers import get_employee_id


def _task_id_of(row: dict) -> str | None:
    """Return the task id (as a string) of an ``account.analytic.line`` row.

    Odoo renders ``task_id`` as a ``[id, display_name]`` many2one pair; the id is
    stringified so it keys against the derived session's string ``task_id``. A
    row with no task collapses to ``None`` and is dropped by the caller.
    """
    value = row.get("task_id")
    if isinstance(value, (list, tuple)) and value:
        return str(value[0])
    return None


def logged_hours_by_task_day(
    client: OdooClient,
    task_ids: Sequence[Any],
    start_date: str,
    end_date: str,
    only_mine: bool = True,
) -> dict[tuple[str, str], float]:
    """Return summed logged hours keyed by ``(task_id, "YYYY-MM-DD")``.

    Read-only: issues one ``search_read`` over ``account.analytic.line`` (plus,
    when ``only_mine``, the employee lookup) and never writes. Only the given
    task ids over the inclusive ``[start_date, end_date]`` range are fetched, and
    each row's ``unit_amount`` is summed in Python by its task and calendar day so
    the review surface can look up the hours already booked against a session's
    task on its day. Rows missing a task or a date are skipped.

    :param client: Odoo API client (any read-capable transport).
    :param task_ids: Task ids to fetch lines for; non-numeric ids are ignored.
    :param start_date: Inclusive range start, ``YYYY-MM-DD``.
    :param end_date: Inclusive range end, ``YYYY-MM-DD``.
    :param only_mine: Restrict to the authenticated user's own timesheets.
    :return: ``{(task_id, day): hours}`` for every task/day with logged time.
    """
    numeric_ids = sorted({int(t) for t in task_ids if str(t).isdigit()})
    if not numeric_ids:
        return {}
    domain = [
        ("task_id", "in", numeric_ids),
        ("date", ">=", start_date),
        ("date", "<=", end_date),
    ]
    if only_mine:
        domain.append(("employee_id", "=", get_employee_id(client, client.uid)))
    rows = client.execute(
        "account.analytic.line",
        "search_read",
        domain,
        fields=["task_id", "date", "unit_amount"],
    )
    totals: dict[tuple[str, str], float] = {}
    for row in rows:
        task = _task_id_of(row)
        day = (row.get("date") or "")[:10]
        if not task or not day:
            continue
        key = (task, day)
        totals[key] = totals.get(key, 0.0) + float(row.get("unit_amount") or 0.0)
    return totals
