"""Odoo API helpers for task time-tracking operations.

This module hosts two kinds of helpers:

* Pure functions (``resolve_many2one``, ``format_chatter``) that accept and
  return only primitives with no side effects.
* Thin Odoo-operation wrappers that take an ``OdooClient`` and issue a single
  well-defined call. They keep command bodies free of raw ``client.execute``
  plumbing so business logic reads at one altitude.
"""

from datetime import date
from typing import Any

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
    return client.execute("account.analytic.line", "create", [vals])


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
        [[timesheet_id]],
        {"unit_amount": unit_amount, "name": description},
    )


def post_chatter_note(client: OdooClient, task_id: int, body: str) -> int:
    """Post a chatter note on project.task and return the message id."""
    return client.execute(
        "project.task",
        "message_post",
        [task_id],
        {
            "body": body,
            "message_type": "comment",
            "subtype_xmlid": "mail.mt_note",
        },
    )


def get_task_chatter(client: OdooClient, task_id: int, limit: int = 100) -> list[dict]:
    """Fetch chatter messages for a task, sorted oldest-first."""
    messages = client.execute(
        "mail.message",
        "search_read",
        [("model", "=", "project.task"), ("res_id", "=", task_id)],
        fields=["id", "date", "author_id", "message_type", "subtype_id", "body"],
        order="date asc",
        limit=limit,
    )
    result = []
    for m in messages:
        author = resolve_many2one(m["author_id"]) or ""
        subtype = resolve_many2one(m["subtype_id"]) or ""
        result.append(
            {
                "id": m["id"],
                "date": m["date"],
                "author": author,
                "type": m["message_type"],
                "subtype": subtype,
                "body": html_to_markdown(m.get("body", "")),
            }
        )
    return result


def get_task_detail(client: OdooClient, task_id: int) -> dict | None:
    """Fetch task fields for a single task; returns None if not found."""
    records = client.execute(
        "project.task",
        "search_read",
        [("id", "=", task_id)],
        fields=[
            "name",
            "description",
            "project_id",
            "stage_id",
            "user_ids",
            "date_deadline",
            "priority",
            "tag_ids",
        ],
        limit=1,
    )
    if not records:
        return None
    r = records[0]

    assignees = [resolve_many2one(uid) for uid in (r.get("user_ids") or [])]
    tags = [resolve_many2one(tag) for tag in (r.get("tag_ids") or [])]

    return {
        "task_id": task_id,
        "name": r["name"],
        "project": resolve_many2one(r.get("project_id")),
        "stage": resolve_many2one(r.get("stage_id")),
        "assignees": assignees,
        "deadline": r.get("date_deadline"),
        "priority": r.get("priority"),
        "tags": tags,
        "description": html_to_markdown(r.get("description") or ""),
    }


def merge_timesheets(
    client: OdooClient, primary_id: int, ids_to_merge: list[int]
) -> None:
    """Sum unit_amount and join descriptions, keep primary, delete others."""
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
        client.execute(
            "account.analytic.line",
            "unlink",
            [ids_to_merge],
        )
