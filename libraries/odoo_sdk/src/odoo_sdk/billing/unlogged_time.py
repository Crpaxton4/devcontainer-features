"""Read-only unlogged-time gap report (issue #378, item 10).

Answers the reconciliation question the manual Jul 1-15 run answered: for each
day and task in a window, how do the hours an upload *would* bill (derived from
the event stream) compare against what is *already logged* in Odoo
(``account.analytic.line``), and what is the delta?

Strictly a composition of existing pieces — it adds no storage and issues no
write:

* **Derived hours** come from the same path the upload takes. Sessions are
  derived with :meth:`~odoo_sdk.state.LocalStateClient.derive_sessions_overlapping`
  (the ``query_sessions`` derivation) and run through
  :func:`~odoo_sdk.billing.upload.upload_sessions` in ``dry_run`` mode, so the
  reported hours carry the *exact* billing transform an upload applies — the
  ``min_session_hours`` floor and ``round_session_hours`` rounding (#355), the
  aborted-run exclusion (#356), and the non-numeric-task skip. The report
  therefore predicts what an upload over the same window would bill, without
  billing anything (``dry_run`` issues no Odoo write).
* **Logged hours** are read with the same server-side ``read_group`` shape the
  ``timesheet_summary`` builtin uses, grouped on both the ``task_id`` and
  ``date:day`` axes at once so each (day, task) cell is summed by Odoo.

Each derived session is bucketed onto its start day (``started_at.date()``) —
the same day :func:`~odoo_sdk.billing.timesheet.reconcile_session` bills it on
— and only days within the requested window are reported. Only rows with a
nonzero delta are returned by default; ``include_all`` keeps the reconciled
(zero-delta) rows too. Window and per-day totals are computed over *all* cells
(reconciled or not), so the totals always describe the whole window.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Optional

from odoo_sdk import OdooTransportError
from odoo_sdk.state import LocalConfig, LocalStateClient, SessionWindow, session_key
from odoo_sdk.utilities.odoo_helpers import get_employee_id, resolve_many2one

from .timesheet_reports import day_label, parse_date, row_hours
from .upload import range_bounds, upload_sessions

#: Message raised when the logged-hours read cannot reach Odoo. The report is
#: pointless offline (it has nothing to reconcile the derived hours against), so
#: an unreachable instance is a single clear error rather than a partial report.
_UNREACHABLE = (
    "unlogged_time_report needs a reachable Odoo instance to read logged "
    "timesheets."
)


def _session_dict(window: SessionWindow) -> dict[str, Any]:
    """Render the minimal session dict the upload path bills from.

    Only the fields :func:`~odoo_sdk.billing.upload.upload_sessions` reads are
    populated (task id, stable key, bounds, duration); events are omitted since
    the dry-run billing never inspects them.
    """
    return {
        "task_id": window.task_id,
        "session_key": session_key(window),
        "started_at": window.started_at.isoformat(),
        "ended_at": window.ended_at.isoformat(),
        "duration_secs": window.duration_seconds,
    }


def _derived_hours_by_day_task(
    client: Any,
    state: LocalStateClient,
    config: LocalConfig,
    start: date,
    end: date,
    start_date: str,
    end_date: str,
) -> dict[tuple[str, int], float]:
    """Bill the window's derived sessions (dry run) into (day, task) buckets.

    The dry run applies the real billing transform but issues no Odoo write, so
    every returned cell is the hours an upload over ``[start, end]`` would bill,
    keyed by ``(ISO start-day, task id)``. Sessions whose start day falls outside
    the window (a session that overlaps the window but began earlier) are dropped
    so the report stays scoped to the requested days.
    """
    windows = state.derive_sessions_overlapping(
        *range_bounds(start_date, end_date), gap_secs=config.session_gap_secs
    )
    preview = upload_sessions(
        client,
        state,
        [_session_dict(window) for window in windows],
        start_date=start_date,
        end_date=end_date,
        dry_run=True,
        config=config,
    )
    day_by_key = {session_key(window): window.started_at.date() for window in windows}
    buckets: dict[tuple[str, int], float] = {}
    for row in preview["rows"]:
        day = day_by_key.get(row["session_key"])
        if day is None or not (start <= day <= end):
            continue
        cell = (day.isoformat(), int(row["task_id"]))
        buckets[cell] = buckets.get(cell, 0.0) + float(row["hours"])
    return buckets


def _logged_hours_by_day_task(
    client: Any, start: date, end: date, only_mine: bool
) -> dict[tuple[str, int], dict[str, Any]]:
    """Sum logged ``account.analytic.line`` hours into (day, task) buckets.

    Uses the ``timesheet_summary`` ``read_group`` shape but over *two* group
    axes at once (``task_id`` and ``date:day``), so Odoo returns one summed cell
    per (task, day). Lines with no task cannot map to a derived session and are
    skipped. Each bucket carries the summed ``hours`` and the task's display
    ``task`` name. An unreachable Odoo surfaces as a single clear error.
    """
    domain = [("date", ">=", start.isoformat()), ("date", "<=", end.isoformat())]
    if only_mine:
        try:
            domain.append(("employee_id", "=", get_employee_id(client, client.uid)))
        except OdooTransportError as exc:
            raise OdooTransportError(_UNREACHABLE) from exc
    try:
        rows = client.execute(
            "account.analytic.line",
            "read_group",
            domain,
            fields=["unit_amount"],
            groupby=["task_id", "date:day"],
            lazy=False,
        )
    except OdooTransportError as exc:
        raise OdooTransportError(_UNREACHABLE) from exc
    buckets: dict[tuple[str, int], dict[str, Any]] = {}
    for row in rows:
        task = row.get("task_id")
        if not task:
            continue
        task_id = task[0] if isinstance(task, (list, tuple)) else task
        cell = (day_label(row), int(task_id))
        bucket = buckets.setdefault(
            cell, {"hours": 0.0, "task": resolve_many2one(task)}
        )
        bucket["hours"] += row_hours(row)
    return buckets


def _row(
    day: str,
    task_id: int,
    derived: float,
    logged: float,
    task_name: Optional[str],
) -> dict[str, Any]:
    """Build one (day, task) report row with a rounded derived/logged/delta."""
    derived_r = round(derived, 2)
    logged_r = round(logged, 2)
    return {
        "day": day,
        "task_id": task_id,
        "task": task_name,
        "derived_hours": derived_r,
        "logged_hours": logged_r,
        "delta": round(derived_r - logged_r, 2),
    }


def _all_rows(
    derived: dict[tuple[str, int], float],
    logged: dict[tuple[str, int], dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build every (day, task) row from the union of derived and logged cells."""
    rows = []
    for cell in derived.keys() | logged.keys():
        day, task_id = cell
        logged_cell = logged.get(cell)
        rows.append(
            _row(
                day,
                task_id,
                derived.get(cell, 0.0),
                logged_cell["hours"] if logged_cell else 0.0,
                logged_cell["task"] if logged_cell else None,
            )
        )
    rows.sort(key=lambda row: (row["day"], row["task_id"]))
    return rows


def _group_by_day(
    rows: list[dict[str, Any]], include_all: bool
) -> list[dict[str, Any]]:
    """Group rows by day, filtering zero-delta rows unless ``include_all``.

    Per-day totals are summed over *all* the day's cells (reconciled included),
    so a shown day's totals describe the whole day; a day with no surviving row
    after the delta filter is omitted entirely.
    """
    by_day: dict[str, dict[str, Any]] = {}
    for row in rows:
        day = by_day.setdefault(
            row["day"], {"day": row["day"], "rows": [], "totals": [0.0, 0.0]}
        )
        day["totals"][0] += row["derived_hours"]
        day["totals"][1] += row["logged_hours"]
        if include_all or row["delta"] != 0:
            day["rows"].append(row)
    days = []
    for day in by_day.values():
        if not day["rows"]:
            continue
        derived, logged = day["totals"]
        days.append(
            {
                "day": day["day"],
                "rows": day["rows"],
                "derived_hours": round(derived, 2),
                "logged_hours": round(logged, 2),
                "delta": round(derived - logged, 2),
            }
        )
    return days


def unlogged_time_report(
    client: Any,
    state: LocalStateClient,
    config: LocalConfig,
    start_date: str,
    end_date: str,
    only_mine: bool = True,
    include_all: bool = False,
) -> dict[str, Any]:
    """Report per (day, task) derived-vs-logged hours and their delta.

    :param client: Odoo API client (read-only; only the logged-hours read hits
        Odoo — the derived side is a local dry-run bill).
    :param state: Local state client the events/sessions derive from.
    :param config: Resolved SDK settings supplying the session gap and the
        billing policy the dry-run bill applies.
    :param start_date: Inclusive window start, ``YYYY-MM-DD``.
    :param end_date: Inclusive window end, ``YYYY-MM-DD``.
    :param only_mine: Restrict logged hours to the authenticated user's employee
        timesheets (matching what the upload would bill).
    :param include_all: Keep reconciled (zero-delta) rows too; by default only
        rows with a nonzero delta are returned.
    :return: A report dict with the echoed window, ``days`` (each a per-day total
        plus its rows), and window ``total_derived_hours`` /
        ``total_logged_hours`` / ``total_delta_hours``.
    :raises ValueError: On a malformed date.
    :raises OdooTransportError: When Odoo is unreachable for the logged read.
    """
    start = parse_date(start_date, "start_date")
    end = parse_date(end_date, "end_date")
    derived = _derived_hours_by_day_task(
        client, state, config, start, end, start_date, end_date
    )
    logged = _logged_hours_by_day_task(client, start, end, only_mine)
    rows = _all_rows(derived, logged)
    total_derived = round(sum(row["derived_hours"] for row in rows), 2)
    total_logged = round(sum(row["logged_hours"] for row in rows), 2)
    return {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "only_mine": only_mine,
        "include_all": include_all,
        "unit": "hours",
        "days": _group_by_day(rows, include_all),
        "total_derived_hours": total_derived,
        "total_logged_hours": total_logged,
        "total_delta_hours": round(total_derived - total_logged, 2),
    }
