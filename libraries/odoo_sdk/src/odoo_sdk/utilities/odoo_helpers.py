"""Odoo API helpers for task time-tracking operations.

This module hosts two kinds of helpers:

* Pure functions (``resolve_many2one``, ``format_chatter``) that accept and
  return only primitives with no side effects.
* Thin Odoo-operation wrappers that take an ``OdooClient`` and issue a single
  well-defined call. They keep command bodies free of raw ``client.execute``
  plumbing so business logic reads at one altitude.
"""

from datetime import date, datetime, timezone
from typing import Any, Optional

from odoo_sdk.client import OdooClient

from .html import html_to_markdown

# Backwards-compatible private alias kept so existing tests that patch
# ``_html_to_markdown`` on this module continue to resolve.
_html_to_markdown = html_to_markdown


def resolve_many2one(field_val: Any) -> Any:
    """Return the display name of a many2one field value.

    Odoo returns many2one values as ``[id, "Display Name"]`` pairs. This pure
    helper extracts the display name, passing through scalars untouched.

    :param field_val: Raw many2one field value or scalar.
    :type field_val: Any
    :return: Display name when a ``[id, name]`` pair is given, else the value.
    :rtype: Any
    """
    if isinstance(field_val, (list, tuple)) and len(field_val) == 2:
        return field_val[1]
    return field_val


def format_chatter(chatter: list[dict]) -> str:
    """Render chatter messages into a plain-text block.

    :param chatter: Chatter message dicts with ``date``/``author``/``body`` keys.
    :type chatter: list[dict]
    :return: Newline-joined, human-readable chatter transcript.
    :rtype: str
    """
    lines: list[str] = []
    for msg in chatter:
        header = (
            f"[{msg.get('date', '')}] {msg.get('author', '')} "
            f"({msg.get('subtype', msg.get('type', ''))})"
        )
        lines.append(header)
        body = msg.get("body", "").strip()
        if body:
            lines.append(body)
        lines.append("")
    return "\n".join(lines).rstrip()


def name_search_projects(
    client: OdooClient, query: str, limit: int = 10
) -> list[dict[str, Any]]:
    """Search projects by name, returning id and name."""
    results = client.execute(
        "project.project", "name_search", query, [], "ilike", limit
    )
    return [{"id": r[0], "name": r[1]} for r in results]


def name_search_tasks(
    client: OdooClient, query: str, project_id: int, limit: int = 10
) -> list[dict[str, Any]]:
    """Search tasks by name within a project, returning id and name."""
    results = client.execute(
        "project.task",
        "name_search",
        query,
        [("project_id", "=", project_id)],
        "ilike",
        limit,
    )
    return [{"id": r[0], "name": r[1]} for r in results]


def get_employee_id(client: OdooClient, uid: int) -> int:
    """Return the hr.employee id for the authenticated user."""
    records = client.execute(
        "hr.employee",
        "search_read",
        [("user_id", "=", uid)],
        fields=["id"],
        limit=1,
    )
    if not records:
        raise RuntimeError(
            f"No hr.employee record found for user id {uid}. "
            "Ensure the user has an employee record in Odoo."
        )
    return records[0]["id"]


def create_timesheet(
    client: OdooClient,
    task_id: int,
    project_id: int,
    employee_id: int,
    today: date,
) -> int:
    """Create a placeholder account.analytic.line and return its id."""
    vals = {
        "name": "[/] Work in progress",
        "unit_amount": 0.0,
        "project_id": project_id,
        "task_id": task_id,
        "date": today.isoformat(),
        "employee_id": employee_id,
    }
    return client.execute("account.analytic.line", "create", vals)


def update_timesheet(
    client: OdooClient,
    timesheet_id: int,
    unit_amount: float,
    description: str,
) -> None:
    """Update the timesheet entry with final elapsed hours and description."""
    client.execute(
        "account.analytic.line",
        "write",
        [timesheet_id],
        {"unit_amount": unit_amount, "name": description},
    )


def post_chatter_note(client: OdooClient, task_id: int, body: str) -> int:
    """Post a chatter note on project.task and return the message id.

    Odoo's ``mail.thread.message_post`` is keyword-only
    (``def message_post(self, *, body='', ...)``). The message options must
    therefore be forwarded as ``execute_kw`` keyword arguments; passing them as
    a trailing positional dict makes Odoo treat the dict as a positional method
    argument and raise ``TypeError`` (see issue #131).
    """
    return client.execute(
        "project.task",
        "message_post",
        [task_id],
        body=body,
        message_type="comment",
        subtype_xmlid="mail.mt_note",
    )


# Odoo ``mail.message`` fields fetched to shape a chatter entry. Shared by the
# per-task chatter fetch and the cross-record chatter search so both apply the
# identical presentation (display-name extraction + HTML-to-Markdown body).
_CHATTER_MESSAGE_FIELDS = [
    "id",
    "date",
    "author_id",
    "message_type",
    "subtype_id",
    "body",
]


def shape_chatter_message(message: dict) -> dict:
    """Normalise one raw ``mail.message`` record into a chatter entry.

    This is the single shaping helper reused by every chatter reader so the
    presentation stays consistent: the ``author_id`` and ``subtype_id`` many2one
    pairs are reduced to their display names, and the HTML ``body`` is converted
    to trimmed Markdown via :func:`html_to_markdown`.

    :param message: Raw ``mail.message`` record carrying at least the fields in
        :data:`_CHATTER_MESSAGE_FIELDS`.
    :type message: dict
    :return: Dict with ``id``, ``date``, ``author``, ``type``, ``subtype`` and a
        Markdown ``body``.
    :rtype: dict
    """
    return {
        "id": message["id"],
        "date": message["date"],
        "author": resolve_many2one(message["author_id"]) or "",
        "type": message["message_type"],
        "subtype": resolve_many2one(message["subtype_id"]) or "",
        "body": html_to_markdown(message.get("body", "")),
    }


def get_task_chatter(client: OdooClient, task_id: int, limit: int = 100) -> list[dict]:
    """Fetch chatter messages for a task, sorted oldest-first."""
    messages = client.execute(
        "mail.message",
        "search_read",
        [("model", "=", "project.task"), ("res_id", "=", task_id)],
        fields=_CHATTER_MESSAGE_FIELDS,
        order="date asc",
        limit=limit,
    )
    return [shape_chatter_message(m) for m in messages]


def search_chatter(
    client: OdooClient,
    query: str,
    model: str | None = None,
    record_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """Full-text search ``mail.message`` bodies, newest-first.

    Builds a ``body ilike <query>`` domain and appends the optional filters that
    were supplied, then reuses :func:`shape_chatter_message` for presentation and
    adds the originating ``res_model`` / ``res_id`` so callers can navigate to the
    source record. Read-only: issues a single ``search_read``.

    :param client: The Odoo API client.
    :type client: OdooClient
    :param query: Substring matched case-insensitively against message bodies.
    :type query: str
    :param model: Optional Odoo model name (e.g. ``project.task``) to restrict
        the search to messages on that model.
    :type model: str | None
    :param record_id: Optional record id to restrict the search to one record's
        conversation; usually paired with ``model``.
    :type record_id: int | None
    :param date_from: Optional inclusive lower bound (``YYYY-MM-DD``) on the
        message timestamp.
    :type date_from: str | None
    :param date_to: Optional inclusive upper bound (``YYYY-MM-DD``) on the message
        timestamp; compared against the start of that day.
    :type date_to: str | None
    :param limit: Maximum number of messages to return, newest first.
    :type limit: int
    :return: Shaped chatter entries, each carrying ``res_model`` and ``res_id``.
    :rtype: list[dict]
    """
    domain: list[tuple[str, str, Any]] = [("body", "ilike", query)]
    if model is not None:
        domain.append(("model", "=", model))
    if record_id is not None:
        domain.append(("res_id", "=", record_id))
    if date_from is not None:
        domain.append(("date", ">=", date_from))
    if date_to is not None:
        domain.append(("date", "<=", date_to))

    messages = client.execute(
        "mail.message",
        "search_read",
        domain,
        fields=[*_CHATTER_MESSAGE_FIELDS, "model", "res_id"],
        order="date desc",
        limit=limit,
    )
    result = []
    for m in messages:
        shaped = shape_chatter_message(m)
        shaped["res_model"] = m.get("model")
        shaped["res_id"] = m.get("res_id")
        result.append(shaped)
    return result


# Base identity fields always fetched for a task, regardless of ``include``.
_TASK_BASE_FIELDS = [
    "name",
    "project_id",
    "stage_id",
    "user_ids",
    "date_deadline",
    "priority",
    "tag_ids",
]

# Extra Odoo fields required to hydrate each opt-in ``include`` selector.
_TASK_INCLUDE_FIELDS = {
    "description": ["description"],
    "dependencies": ["depend_on_ids", "dependent_ids"],
    "timesheets": ["timesheet_ids"],
    "subtasks": ["child_ids"],
}


def _task_related_stages(client: OdooClient, task_ids: list[int]) -> list[list]:
    """Read ``[id, name, stage]`` rows for the given task ids, order preserved."""
    if not task_ids:
        return []
    records = client.execute(
        "project.task",
        "read",
        task_ids,
        fields=["name", "stage_id"],
    )
    by_id = {rec["id"]: rec for rec in records}
    rows = []
    for tid in task_ids:
        rec = by_id.get(tid)
        if rec is None:
            continue
        rows.append([tid, rec["name"], resolve_many2one(rec.get("stage_id"))])
    return rows


def _task_timesheets(client: OdooClient, timesheet_ids: list[int]) -> list[dict]:
    """Read timesheet entries as date / employee / hours / name dicts."""
    if not timesheet_ids:
        return []
    records = client.execute(
        "account.analytic.line",
        "read",
        timesheet_ids,
        fields=["date", "employee_id", "unit_amount", "name"],
    )
    return [
        {
            "date": rec.get("date"),
            "employee": resolve_many2one(rec.get("employee_id")),
            "hours": rec.get("unit_amount"),
            "name": rec.get("name"),
        }
        for rec in records
    ]


def _task_subtasks(client: OdooClient, child_ids: list[int]) -> list[dict]:
    """Read subtasks as id / name / stage / assignees dicts."""
    if not child_ids:
        return []
    records = client.execute(
        "project.task",
        "read",
        child_ids,
        fields=["name", "stage_id", "user_ids"],
    )
    return [
        {
            "id": rec["id"],
            "name": rec["name"],
            "stage": resolve_many2one(rec.get("stage_id")),
            "assignees": [resolve_many2one(uid) for uid in (rec.get("user_ids") or [])],
        }
        for rec in records
    ]


def _task_detail_fields(selected: list[str]) -> list[str]:
    """Build the Odoo ``fields`` list for the selected ``include`` keys."""
    fields = list(_TASK_BASE_FIELDS)
    extra = [
        field
        for key in selected
        for field in _TASK_INCLUDE_FIELDS.get(key, [])
        if field not in _TASK_BASE_FIELDS
    ]
    fields.extend(dict.fromkeys(extra))
    return fields


def _task_extra_detail(
    client: OdooClient, record: dict, selected: list[str]
) -> dict[str, Any]:
    """Assemble the opt-in detail collections for the selected ``include`` keys."""
    extra: dict[str, Any] = {}
    if "description" in selected:
        extra["description"] = html_to_markdown(record.get("description") or "")
    if "dependencies" in selected:
        extra["blocked_by"] = _task_related_stages(
            client, record.get("depend_on_ids") or []
        )
        extra["blocks"] = _task_related_stages(
            client, record.get("dependent_ids") or []
        )
    if "timesheets" in selected:
        extra["timesheets"] = _task_timesheets(
            client, record.get("timesheet_ids") or []
        )
    if "subtasks" in selected:
        extra["subtasks"] = _task_subtasks(client, record.get("child_ids") or [])
    return extra


def get_task_detail(
    client: OdooClient, task_id: int, include: list[str] | None = None
) -> dict | None:
    """Fetch task fields for a single task; returns None if not found.

    Base identity fields (name, project, stage, assignees, deadline, priority,
    tags) are always present. Each entry in ``include`` opts into an extra,
    more expensive collection. When ``include`` is ``None`` the default is
    description only, and no relation fields are fetched.

    :param client: The Odoo API client.
    :type client: OdooClient
    :param task_id: The project.task id to fetch.
    :type task_id: int
    :param include: Opt-in selectors: ``description``, ``dependencies``,
        ``timesheets``, ``subtasks``. Defaults to ``["description"]``.
    :type include: list[str] | None
    :return: Task detail dict, or ``None`` if the task does not exist.
    :rtype: dict | None
    """
    selected = ["description"] if include is None else include

    records = client.execute(
        "project.task",
        "search_read",
        [("id", "=", task_id)],
        fields=_task_detail_fields(selected),
        limit=1,
    )
    if not records:
        return None
    r = records[0]

    assignees = [resolve_many2one(uid) for uid in (r.get("user_ids") or [])]
    tags = [resolve_many2one(tag) for tag in (r.get("tag_ids") or [])]

    result = {
        "task_id": task_id,
        "name": r["name"],
        "project": resolve_many2one(r.get("project_id")),
        "stage": resolve_many2one(r.get("stage_id")),
        "assignees": assignees,
        "deadline": r.get("date_deadline"),
        "priority": r.get("priority"),
        "tags": tags,
    }
    result.update(_task_extra_detail(client, r, selected))
    return result


# --- Unbilled-hours reporting (read-only) --------------------------------------
#
# The billing state of a timesheet line lives in two ``account.analytic.line``
# fields contributed by Odoo's ``sale_timesheet`` module, so their presence is
# *edition-dependent*. ``get_unbilled_hours`` probes for them with ``fields_get``
# before querying and adapts its semantics to what the database actually exposes.

#: ``account.analytic.line`` field holding the customer invoice a timesheet line
#: was billed on. ``False`` means the line has not been invoiced yet.
_UNBILLED_INVOICE_ID_FIELD = "timesheet_invoice_id"

#: ``account.analytic.line`` field classifying a timesheet line's billability
#: (e.g. ``billable_time``, ``non_billable``). Surfaced per line in full mode.
_UNBILLED_INVOICE_TYPE_FIELD = "timesheet_invoice_type"

#: Fields always projected for each returned timesheet line, in both probe modes.
_UNBILLED_BASE_FIELDS = [
    "id",
    "date",
    "employee_id",
    "project_id",
    "task_id",
    "unit_amount",
    "name",
]

#: Exact message raised when neither ``sale_timesheet`` billing field exists, so
#: callers (and tests) get one stable, actionable string. Raised as a
#: ``ValueError`` which the MCP error boundary renders as a structured payload.
MISSING_UNBILLED_CAPABILITY_MESSAGE = (
    "unbilled_hours is unavailable on this Odoo database: account.analytic.line "
    "exposes neither 'timesheet_invoice_id' nor 'timesheet_invoice_type'. These "
    "fields are contributed by the 'sale_timesheet' module; install/enable the "
    "Sales-Timesheet integration to report unbilled hours."
)


def _probe_unbilled_fields(client: OdooClient) -> tuple[bool, bool]:
    """Report which ``sale_timesheet`` billing fields exist on the timesheet model.

    ``fields_get`` returns metadata only for the requested fields that actually
    exist, so membership of each name in the response is a capability check.

    :param client: The Odoo API client.
    :type client: OdooClient
    :return: ``(has_invoice_id, has_invoice_type)`` presence flags.
    :rtype: tuple[bool, bool]
    """
    meta = client.execute(
        "account.analytic.line",
        "fields_get",
        [_UNBILLED_INVOICE_ID_FIELD, _UNBILLED_INVOICE_TYPE_FIELD],
    )
    return (
        _UNBILLED_INVOICE_ID_FIELD in meta,
        _UNBILLED_INVOICE_TYPE_FIELD in meta,
    )


def _validate_iso_date(value: str | None, label: str) -> None:
    """Reject a non-``YYYY-MM-DD`` date so filters never silently mis-compare.

    ``None`` (the "unbounded" sentinel) passes through untouched.

    :param value: Candidate ISO date string, or ``None``.
    :type value: str | None
    :param label: Parameter name used in the error message.
    :type label: str
    :raises ValueError: When ``value`` is not a valid ``YYYY-MM-DD`` string.
    :return: None.
    :rtype: None
    """
    if value is None:
        return
    try:
        # Round-tripping through ``isoformat`` rejects basic-ISO inputs such as
        # ``"20260701"`` that ``fromisoformat`` accepts but Odoo's canonical
        # ``YYYY-MM-DD`` date strings would silently mis-compare against.
        canonical: str | None = date.fromisoformat(value).isoformat()
    except (ValueError, TypeError):
        canonical = None
    if canonical != value:
        raise ValueError(
            f"{label} must be an ISO date string 'YYYY-MM-DD', got {value!r}."
        )


def _unbilled_domain(
    full: bool,
    start_date: str | None,
    end_date: str | None,
    project_id: int | None,
) -> list[tuple]:
    """Build the ``search_read`` domain selecting unbilled timesheet lines.

    ``project_id != False`` restricts the query to genuine timesheet lines
    (excluding other analytic lines). The billing predicate depends on the probe
    outcome: in full mode an *uninvoiced* line is ``timesheet_invoice_id = False``;
    in fallback mode (only one billing field present) the proxy is
    ``so_line = False`` — not yet attached to a sale order for invoicing.

    :param full: True when both billing fields exist (full semantics).
    :type full: bool
    :param start_date: Inclusive lower ``date`` bound (``YYYY-MM-DD``) or None.
    :type start_date: str | None
    :param end_date: Inclusive upper ``date`` bound (``YYYY-MM-DD``) or None.
    :type end_date: str | None
    :param project_id: Restrict to this ``project.project`` id, or None.
    :type project_id: int | None
    :return: Odoo search domain as a list of triples.
    :rtype: list[tuple]
    """
    domain: list[tuple] = [("project_id", "!=", False)]
    if full:
        domain.append((_UNBILLED_INVOICE_ID_FIELD, "=", False))
    else:
        domain.append(("so_line", "=", False))
    if start_date is not None:
        domain.append(("date", ">=", start_date))
    if end_date is not None:
        domain.append(("date", "<=", end_date))
    if project_id is not None:
        domain.append(("project_id", "=", project_id))
    return domain


def _unbilled_row(record: dict, full: bool) -> dict:
    """Shape one raw ``account.analytic.line`` record into an output line.

    Many2one values are flattened to their display name. ``invoice_type`` is
    included only in full mode; its presence signals which semantics applied.

    :param record: Raw ``search_read`` record.
    :type record: dict
    :param full: Whether full (both-field) semantics produced this row.
    :type full: bool
    :return: Output line dict with resolved names and decimal hours.
    :rtype: dict
    """
    row = {
        "id": record["id"],
        "date": record.get("date"),
        "employee": resolve_many2one(record.get("employee_id")),
        "project": resolve_many2one(record.get("project_id")),
        "task": resolve_many2one(record.get("task_id")),
        "hours": record.get("unit_amount"),
        "name": record.get("name"),
    }
    if full:
        row["invoice_type"] = record.get(_UNBILLED_INVOICE_TYPE_FIELD)
    return row


def _resolve_unbilled_mode(client: OdooClient) -> bool:
    """Probe billing capability, returning the *full-mode* flag or raising.

    :param client: The Odoo API client.
    :type client: OdooClient
    :raises ValueError: When neither billing field exists.
    :return: True when both fields exist (full mode); False for the fallback.
    :rtype: bool
    """
    has_invoice_id, has_invoice_type = _probe_unbilled_fields(client)
    if not has_invoice_id and not has_invoice_type:
        raise ValueError(MISSING_UNBILLED_CAPABILITY_MESSAGE)
    return has_invoice_id and has_invoice_type


def _row_hours(record: dict) -> float:
    """Return a line's ``unit_amount`` as hours, treating missing/empty as zero.

    :param record: Raw ``account.analytic.line`` record.
    :type record: dict
    :return: Decimal hours logged on the line.
    :rtype: float
    """
    return record.get("unit_amount") or 0.0


def _unbilled_envelope(records: list[dict], full: bool) -> dict:
    """Assemble the summary envelope from raw records under the given mode.

    :param records: Raw ``search_read`` records.
    :type records: list[dict]
    :param full: Whether full (both-field) semantics were used.
    :type full: bool
    :return: ``{"mode", "count", "total_hours", "lines"}`` summary envelope.
    :rtype: dict
    """
    lines = [_unbilled_row(record, full) for record in records]
    total_hours = round(sum(_row_hours(record) for record in records), 2)
    return {
        "mode": "full" if full else "fallback",
        "count": len(lines),
        "total_hours": total_hours,
        "lines": lines,
    }


def get_unbilled_hours(
    client: OdooClient,
    start_date: str | None = None,
    end_date: str | None = None,
    project_id: int | None = None,
) -> dict:
    """Return timesheet hours logged but not yet invoiced, as a summary envelope.

    A ``fields_get`` capability probe on ``account.analytic.line`` decides the
    meaning of *unbilled*:

    * **Full** (both ``timesheet_invoice_id`` and ``timesheet_invoice_type``
      present): a line is unbilled when ``timesheet_invoice_id = False`` — it has
      not been posted to any customer invoice. Each returned line also carries
      ``invoice_type`` (its ``timesheet_invoice_type``) so callers can tell
      billable time from non-billable.
    * **Fallback** (only one of the two present): billing state cannot be read
      directly, so unbilled is approximated as ``so_line = False`` — the line is
      not linked to a sale order line for invoicing. Rows omit ``invoice_type``.
    * **Neither present**: raises :class:`ValueError` with
      :data:`MISSING_UNBILLED_CAPABILITY_MESSAGE`.

    Only genuine timesheet lines are considered (``project_id != False``). Dates
    are inclusive ``YYYY-MM-DD`` strings; ``hours`` and ``total_hours`` are
    decimal hours (``unit_amount``).

    :param client: The Odoo API client.
    :type client: OdooClient
    :param start_date: Inclusive lower ``date`` bound (``YYYY-MM-DD``) or None.
    :type start_date: str | None
    :param end_date: Inclusive upper ``date`` bound (``YYYY-MM-DD``) or None.
    :type end_date: str | None
    :param project_id: Restrict to this ``project.project`` id, or None for all.
    :type project_id: int | None
    :raises ValueError: On a malformed date or when no billing field exists.
    :return: ``{"mode", "count", "total_hours", "lines"}`` summary envelope.
    :rtype: dict
    """
    _validate_iso_date(start_date, "start_date")
    _validate_iso_date(end_date, "end_date")

    full = _resolve_unbilled_mode(client)

    fields = list(_UNBILLED_BASE_FIELDS)
    if full:
        fields.append(_UNBILLED_INVOICE_TYPE_FIELD)

    records = client.execute(
        "account.analytic.line",
        "search_read",
        _unbilled_domain(full, start_date, end_date, project_id),
        fields=fields,
        order="date asc",
    )
    return _unbilled_envelope(records, full)


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
    """
    all_ids = [primary_id] + ids_to_merge
    records = client.execute(
        "account.analytic.line",
        "read",
        [all_ids],
        {"fields": ["id", "unit_amount", "name"]},
    )
    total_hours = sum(r["unit_amount"] for r in records)
    descriptions = list(
        dict.fromkeys(
            r["name"] for r in records if r["name"] != "[/] Work in progress"
        )
    )
    merged_desc = " | ".join(descriptions) if descriptions else "[/] Work in progress"
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


# ── task_aging (read-only) ────────────────────────────────────────────────────

# Odoo serializes datetime fields as naive UTC strings in this exact format.
_ODOO_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"

# Fields read from project.task to compute aging; all cheap scalars/many2ones.
_TASK_AGING_FIELDS = [
    "id",
    "name",
    "project_id",
    "stage_id",
    "create_date",
    "date_last_stage_update",
]


def _odoo_days_since(value: Any, now: datetime) -> Optional[int]:
    """Whole days elapsed from an Odoo datetime string to ``now``.

    Odoo datetimes arrive as naive UTC strings (``"YYYY-MM-DD HH:MM:SS"``). A
    falsy value (``False``/``None``/``""`` — Odoo's empty datetime) yields
    ``None`` so callers can apply their own fallback. An unexpectedly-formatted
    string also yields ``None`` rather than raising, so one malformed row cannot
    abort the whole read-only report. The result is floored to whole days
    (``timedelta.days``).

    :param value: Raw Odoo datetime string, or a falsy empty value.
    :type value: Any
    :param now: UTC-aware reference "now".
    :type now: datetime
    :return: Whole days elapsed, or ``None`` when ``value`` is empty or
        unparseable.
    :rtype: Optional[int]
    """
    if not value:
        return None
    try:
        parsed = datetime.strptime(value, _ODOO_DATETIME_FORMAT)
    except (ValueError, TypeError):
        return None
    return (now - parsed.replace(tzinfo=timezone.utc)).days


def _task_aging_record(row: dict, now: datetime) -> dict:
    """Project one raw ``project.task`` row into an aging record.

    ``days_open`` comes from ``create_date``; ``days_in_stage`` from
    ``date_last_stage_update``. When the stage-update timestamp is missing/False
    it falls back to ``create_date`` (so ``days_in_stage`` equals ``days_open``).
    """
    days_open = _odoo_days_since(row.get("create_date"), now)
    days_in_stage = _odoo_days_since(row.get("date_last_stage_update"), now)
    if days_in_stage is None:
        days_in_stage = days_open
    return {
        "task_id": row["id"],
        "name": row.get("name"),
        "project": resolve_many2one(row.get("project_id")),
        "stage": resolve_many2one(row.get("stage_id")),
        "days_open": days_open,
        "days_in_stage": days_in_stage,
    }


def _task_aging_sort_key(record: dict) -> tuple:
    """Sort key for stalest-first ordering (used with ``reverse=True``).

    Primary key is ``days_in_stage`` (descending), tie-broken by ``days_open``
    (descending), then ``task_id`` (ascending, via negation) for determinism.
    Unknown (``None``) day counts sort as ``-1`` so they land last.
    """
    days_in_stage = record["days_in_stage"]
    days_open = record["days_open"]
    return (
        days_in_stage if days_in_stage is not None else -1,
        days_open if days_open is not None else -1,
        -record["task_id"],
    )


def get_task_aging(
    client: OdooClient,
    project_id: Optional[int] = None,
    stage: Optional[str] = None,
    limit: int = 20,
    now: Optional[datetime] = None,
) -> list[dict]:
    """List open ``project.task`` records ordered stalest-first.

    "Open" means the task's kanban stage is not folded
    (``stage_id.fold = False``) and the task is not archived (search_read's
    default ``active = True`` filter). Folded stages are the collapsed
    "Done"/"Cancelled" columns Odoo uses to mark completed work.

    For each task, ``days_open`` is the whole days since ``create_date`` and
    ``days_in_stage`` the whole days since ``date_last_stage_update``; a
    missing/False ``date_last_stage_update`` falls back to ``create_date``.
    Results are sorted stalest-first: ``days_in_stage`` descending, ties broken
    by ``days_open`` descending.

    ``limit`` bounds the query at the database (the tasks with the oldest
    ``date_last_stage_update`` are fetched), so at most ``limit`` records are
    returned. ``project_id`` filters by exact project id; ``stage`` is a
    case-insensitive substring match against the stage's display name.

    :param client: The Odoo API client.
    :type client: OdooClient
    :param project_id: Restrict to one project id, or ``None`` for all.
    :type project_id: Optional[int]
    :param stage: Case-insensitive stage-name substring filter, or ``None``.
    :type stage: Optional[str]
    :param limit: Maximum number of tasks to return.
    :type limit: int
    :param now: UTC-aware reference "now"; defaults to the current time.
        Injected by tests for deterministic day counts.
    :type now: Optional[datetime]
    :return: Aging records, stalest-first.
    :rtype: list[dict]
    """
    if now is None:
        now = datetime.now(timezone.utc)

    domain: list[Any] = [("stage_id.fold", "=", False)]
    if project_id is not None:
        domain.append(("project_id", "=", project_id))
    if stage:
        domain.append(("stage_id.name", "ilike", stage))

    rows = client.execute(
        "project.task",
        "search_read",
        domain,
        fields=_TASK_AGING_FIELDS,
        order="date_last_stage_update asc",
        limit=limit,
    )
    records = [_task_aging_record(row, now) for row in rows]
    records.sort(key=_task_aging_sort_key, reverse=True)
    return records
