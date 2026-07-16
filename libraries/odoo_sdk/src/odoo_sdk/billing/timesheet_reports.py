"""Read-only timesheet aggregation for reporting and billing.

Sums logged hours (``account.analytic.line.unit_amount``) over an inclusive
date range using Odoo's server-side ``read_group``, collapsed onto a single
axis (project, client, task, or calendar day). Strictly read-only: only
``read_group`` and ``read`` are ever issued — nothing is written.
"""

from datetime import datetime
from typing import Any

from odoo_sdk.client import OdooClient
from odoo_sdk.utilities.odoo_helpers import get_employee_id, resolve_many2one

#: Public grouping axes accepted by :func:`timesheet_summary`.
VALID_GROUP_BY = ("project", "client", "task", "day")

#: Public group_by axis -> the Odoo ``read_group`` groupby specifier. ``client``
#: groups by project first, then re-aggregates onto each project's partner.
_GROUP_FIELD = {
    "project": "project_id",
    "task": "task_id",
    "day": "date:day",
    "client": "project_id",
}


def parse_date(value: Any, label: str) -> Any:
    """Parse a ``YYYY-MM-DD`` string into a date; raise a ``label``-naming error.

    ``label`` (e.g. ``start_date``) names the offending parameter in the raised
    ``ValueError`` when ``value`` is not a ``YYYY-MM-DD`` date string.
    """
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        raise ValueError(
            f"Invalid {label} {value!r}: expected a YYYY-MM-DD date string."
        ) from None


def _read_group_hours(
    client: OdooClient, domain: list, groupby: str
) -> list[dict]:
    """Sum ``unit_amount`` over ``domain``, grouped by a single specifier."""
    return client.execute(
        "account.analytic.line",
        "read_group",
        domain,
        fields=["unit_amount"],
        groupby=[groupby],
        lazy=False,
    )


def row_hours(row: dict) -> float:
    """Return the summed hours for one ``read_group`` row (0.0 when absent)."""
    return float(row.get("unit_amount") or 0.0)


def _row_count(row: dict) -> int:
    """Return the record count for one ``read_group`` row (0 when absent)."""
    return int(row.get("__count") or 0)


def day_label(row: dict) -> Any:
    """Return the ISO ``YYYY-MM-DD`` day for a ``date:day`` group row.

    Odoo's ``read_group`` renders the ``date:day`` value as a locale-formatted
    string, but also exposes machine-readable boundaries under ``__range``. The
    ISO ``from`` boundary is preferred so labels are always ``YYYY-MM-DD``;
    absent that, the raw value is passed through. Only one groupby is in play, so
    the single ``__range`` entry is read without hard-coding its key name (which
    varies across Odoo versions).
    """
    for boundary in (row.get("__range") or {}).values():
        start = (boundary or {}).get("from")
        if start:
            return start[:10]
    return row.get("date:day") or None


def _simple_groups(rows: list[dict], group_by: str) -> list[dict]:
    """Shape ``read_group`` rows into ``{label, hours, entries}`` for one axis.

    Handles ``project``/``task`` (many2one display name) and ``day`` (ISO date).
    """
    field = _GROUP_FIELD[group_by]
    groups = []
    for row in rows:
        if group_by == "day":
            label = day_label(row)
        else:
            label = resolve_many2one(row.get(field)) or None
        groups.append(
            {"label": label, "hours": row_hours(row), "entries": _row_count(row)}
        )
    return groups


def _project_partners(client: OdooClient, project_ids: list[int]) -> dict:
    """Map each project id to its partner display name (``None`` when unset)."""
    unique_ids = list(dict.fromkeys(project_ids))
    if not unique_ids:
        return {}
    records = client.execute(
        "project.project",
        "read",
        unique_ids,
        fields=["partner_id"],
    )
    return {
        rec["id"]: resolve_many2one(rec.get("partner_id")) or None
        for rec in records
    }


def _project_id_of(row: dict) -> Any:
    """Return the project id in a project-grouped row, or ``None`` when unset."""
    value = row.get("project_id")
    if isinstance(value, (list, tuple)) and value:
        return value[0]
    return None


def _client_groups(client: OdooClient, rows: list[dict]) -> list[dict]:
    """Re-aggregate project-grouped rows onto each project's partner (client).

    Rows arrive grouped by ``project_id``; each project's partner is resolved
    with one ``read`` and the hours/entries are summed per partner. Rows with no
    project — or a project with no partner — collapse under a ``None`` label.
    """
    partner_by_project = _project_partners(
        client, [pid for pid in map(_project_id_of, rows) if pid is not None]
    )
    accumulated: dict = {}
    for row in rows:
        project_id = _project_id_of(row)
        label = partner_by_project.get(project_id) if project_id is not None else None
        bucket = accumulated.setdefault(label, {"hours": 0.0, "entries": 0})
        bucket["hours"] += row_hours(row)
        bucket["entries"] += _row_count(row)
    return [
        {"label": label, "hours": data["hours"], "entries": data["entries"]}
        for label, data in accumulated.items()
    ]


def timesheet_summary(
    client: OdooClient,
    start_date: str,
    end_date: str,
    group_by: str = "project",
    only_mine: bool = True,
) -> dict:
    """Summarize logged timesheet hours over an inclusive ``YYYY-MM-DD`` range.

    ``group_by`` is one of :data:`VALID_GROUP_BY`; ``only_mine`` restricts to the
    authenticated user's employee timesheets. Returns a summary dict with
    per-group hours/entries and a grand total, and raises ``ValueError`` on an
    invalid ``group_by`` or a malformed date.
    """
    if group_by not in VALID_GROUP_BY:
        raise ValueError(
            f"Invalid group_by {group_by!r}: expected one of "
            "'project', 'client', 'task', 'day'."
        )
    start = parse_date(start_date, "start_date")
    end = parse_date(end_date, "end_date")

    domain = [("date", ">=", start.isoformat()), ("date", "<=", end.isoformat())]
    if only_mine:
        domain.append(("employee_id", "=", get_employee_id(client, client.uid)))

    rows = _read_group_hours(client, domain, _GROUP_FIELD[group_by])
    if group_by == "client":
        groups = _client_groups(client, rows)
    else:
        groups = _simple_groups(rows, group_by)

    for group in groups:
        group["hours"] = round(group["hours"], 2)
    total_hours = round(sum(group["hours"] for group in groups), 2)

    return {
        "group_by": group_by,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "only_mine": only_mine,
        "unit": "hours",
        "groups": groups,
        "total_hours": total_hours,
    }
