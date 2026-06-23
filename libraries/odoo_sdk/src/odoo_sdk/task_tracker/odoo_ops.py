"""Odoo API helpers for task time-tracking operations."""

from datetime import date
from typing import Any

from odoo_sdk.client import OdooClient


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
