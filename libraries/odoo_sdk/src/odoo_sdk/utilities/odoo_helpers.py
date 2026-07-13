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
