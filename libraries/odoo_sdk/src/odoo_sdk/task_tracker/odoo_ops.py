"""Odoo API helpers for task time-tracking operations."""

import io
from datetime import date
from typing import Any

from markitdown import MarkItDown

from odoo_sdk.client import OdooClient

_md_converter = MarkItDown()


def _html_to_markdown(html: str) -> str:
    if not html:
        return ""
    result = _md_converter.convert_stream(io.BytesIO(html.encode()), file_extension=".html")
    return result.text_content.strip()


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
        [[("user_id", "=", uid)]],
        {"fields": ["id"], "limit": 1},
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
        [[("res_model", "=", "project.task"), ("res_id", "=", task_id)]],
        {
            "fields": ["id", "date", "author_id", "message_type", "subtype_id", "body"],
            "order": "date asc",
            "limit": limit,
        },
    )
    result = []
    for m in messages:
        author = m["author_id"][1] if isinstance(m["author_id"], (list, tuple)) else (m["author_id"] or "")
        subtype = m["subtype_id"][1] if isinstance(m["subtype_id"], (list, tuple)) else (m["subtype_id"] or "")
        result.append({
            "id": m["id"],
            "date": m["date"],
            "author": author,
            "type": m["message_type"],
            "subtype": subtype,
            "body": _html_to_markdown(m.get("body", "")),
        })
    return result


def get_task_detail(client: OdooClient, task_id: int) -> dict | None:
    """Fetch task fields for a single task; returns None if not found."""
    records = client.execute(
        "project.task",
        "search_read",
        [[("id", "=", task_id)]],
        {
            "fields": ["name", "description", "project_id", "stage_id", "user_ids", "date_deadline", "priority", "tag_ids"],
            "limit": 1,
        },
    )
    if not records:
        return None
    r = records[0]

    def _name(field_val):
        if isinstance(field_val, (list, tuple)) and len(field_val) == 2:
            return field_val[1]
        return field_val

    assignees = []
    for uid in (r.get("user_ids") or []):
        if isinstance(uid, (list, tuple)):
            assignees.append(uid[1])
        else:
            assignees.append(uid)

    tags = []
    for tag in (r.get("tag_ids") or []):
        if isinstance(tag, (list, tuple)):
            tags.append(tag[1])
        else:
            tags.append(tag)

    return {
        "task_id": task_id,
        "name": r["name"],
        "project": _name(r.get("project_id")),
        "stage": _name(r.get("stage_id")),
        "assignees": assignees,
        "deadline": r.get("date_deadline"),
        "priority": r.get("priority"),
        "tags": tags,
        "description": _html_to_markdown(r.get("description") or ""),
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
        dict.fromkeys(r["name"] for r in records if r["name"] != "[/] Work in progress")
    )
    merged_desc = " | ".join(descriptions) if descriptions else "[/] Work in progress"
    update_timesheet(client, primary_id, total_hours, merged_desc)
    if ids_to_merge:
        client.execute(
            "account.analytic.line",
            "unlink",
            [ids_to_merge],
        )
