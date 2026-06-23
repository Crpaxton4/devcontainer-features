from datetime import date
from typing import Any, Optional

from pydantic import BaseModel

from ..command import Command
from odoo_sdk.task_tracker.env_check import assert_odoo_devcontainer
from odoo_sdk.task_tracker.odoo_ops import (
    create_timesheet,
    get_employee_id,
    name_search_projects,
    name_search_tasks,
    post_chatter_note,
)
from odoo_sdk.task_tracker.state import TaskStateDB


class _SelectProject(BaseModel):
    selection: int


class _SelectTask(BaseModel):
    selection: int


class _ConfirmTask(BaseModel):
    confirmed: bool


async def _disambiguate(ctx: Any, message: str, items: list[dict], schema_cls: type) -> Optional[dict]:
    """Prompt user to pick one item from a list; return item or None on cancel/bad index."""
    numbered = "\n".join(f"{i + 1}. {item['name']}" for i, item in enumerate(items))
    result = await ctx.elicit(f"{message}\n{numbered}\nSelect number:", schema_cls)
    if result.action != "accept":
        return None
    idx = result.data.selection - 1
    if not (0 <= idx < len(items)):
        return None
    return items[idx]


async def _resolve_project(ctx: Any, client: Any, query: str) -> tuple[Optional[dict], Optional[str]]:
    """Return (project, error_string) — exactly one will be non-None."""
    projects = name_search_projects(client, query, limit=10)
    if not projects:
        return None, f"No projects found matching {query!r}."
    if len(projects) == 1:
        return projects[0], None
    project = await _disambiguate(ctx, "Multiple projects found:", projects, _SelectProject)
    if project is None:
        return None, "Project selection cancelled."
    return project, None


async def _resolve_task(ctx: Any, client: Any, query: str, project_id: int, project_name: str) -> tuple[Optional[dict], Optional[str]]:
    """Return (task, error_string) — exactly one will be non-None."""
    tasks = name_search_tasks(client, query, project_id, limit=10)
    if not tasks:
        return None, f"No tasks found matching {query!r} in project {project_name!r}."
    if len(tasks) == 1:
        return tasks[0], None
    task = await _disambiguate(ctx, "Multiple tasks found:", tasks, _SelectTask)
    if task is None:
        return None, "Task selection cancelled."
    return task, None


class StartTaskCommand(Command):
    """Start time-tracking a project task, creating a placeholder timesheet entry."""

    _name = "start_task"
    _description = (
        "Begin tracking time on an Odoo project.task. Searches for the project and task "
        "by name, prompts for disambiguation when multiple matches exist, and always asks "
        "for confirmation before starting. Creates a placeholder timesheet entry in Odoo."
    )

    async def execute(
        self,
        task_name_query: str,
        ctx: Any,
        project_name_query: Optional[str] = None,
    ) -> dict[str, Any]:
        """Start a task tracking session.

        :param task_name_query: Task name substring to search for.
        :param ctx: FastMCP Context for elicitation.
        :param project_name_query: Optional project name substring to narrow search.
        :return: Session details including task name, project, and started_at.
        """
        assert_odoo_devcontainer()

        project, err = await _resolve_project(ctx, self._client, project_name_query or "")
        if err:
            return {"error": err}

        task, err = await _resolve_task(ctx, self._client, task_name_query, project["id"], project["name"])
        if err:
            return {"error": err}

        confirm = await ctx.elicit(
            f"Start tracking time on task:\n  Task: {task['name']}\n  Project: {project['name']}\n\nConfirm?",
            _ConfirmTask,
        )
        if confirm.action != "accept" or not confirm.data.confirmed:
            return {"error": "Task start cancelled."}

        db = TaskStateDB()
        existing = db.get_active_session(task["id"])
        if existing is not None:
            return {
                "error": (
                    f"Task {task['name']!r} already has an active session "
                    f"(id={existing.id}, state={existing.state.value})."
                )
            }

        uid = self._client.uid
        cached_eid = db.get_setting("employee_id")
        if cached_eid is not None:
            employee_id = int(cached_eid)
        else:
            employee_id = get_employee_id(self._client, uid)
            db.set_setting("employee_id", str(employee_id))

        timesheet_id = create_timesheet(
            self._client, task["id"], project["id"], employee_id, date.today()
        )
        post_chatter_note(self._client, task["id"], "Work started on this task.")
        session = db.create_session(
            task_id=task["id"],
            task_name=task["name"],
            project_id=project["id"],
            project_name=project["name"],
            timesheet_id=timesheet_id,
        )

        return {
            "session_id": session.id,
            "task_id": task["id"],
            "task_name": task["name"],
            "project_name": project["name"],
            "started_at": session.started_at.isoformat(),
            "timesheet_id": timesheet_id,
        }
