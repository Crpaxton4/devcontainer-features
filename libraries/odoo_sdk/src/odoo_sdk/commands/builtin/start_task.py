import re
import subprocess
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


class _SelectBranch(BaseModel):
    selection: int


def _current_branch() -> Optional[str]:
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True,
    )
    name = result.stdout.strip()
    return name if result.returncode == 0 and name != "HEAD" else None


def _list_local_branches() -> list[str]:
    result = subprocess.run(
        ["git", "branch", "--format=%(refname:short)"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return []
    branches = [b.strip() for b in result.stdout.splitlines() if b.strip() and "#" not in b]
    return sorted(branches, key=lambda b: (len(b), b))


def _is_dirty() -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True, text=True,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def _create_task_branch(branch_name: str, base_branch: str) -> None:
    stashed = False
    if _is_dirty():
        subprocess.run(["git", "stash", "push", "-m", f"auto-stash: {branch_name}"], check=True)
        stashed = True
    try:
        subprocess.run(["git", "checkout", "-b", branch_name, base_branch], check=True)
    finally:
        if stashed:
            subprocess.run(["git", "stash", "pop"], check=True)


async def _generate_branch_description(ctx: Any, task_name: str, project_name: str) -> str:
    response = await ctx.sample(
        f"Generate a git branch name suffix for this task.\n"
        f"Rules: lowercase only, hyphens instead of spaces/special chars, max 45 chars, no leading/trailing hyphens.\n"
        f"Output ONLY the suffix text, nothing else.\n"
        f"Task: {task_name}\nProject: {project_name}",
        max_tokens=30,
    )
    raw = response.text.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")[:45]
    if not slug:
        slug = re.sub(r"[^a-z0-9]+", "-", task_name.lower()).strip("-")[:45]
    return slug


async def _setup_task_branch(
    ctx: Any, task: dict, project: dict
) -> tuple[Optional[str], Optional[str]]:
    task_id = task["id"]
    current = _current_branch()
    if current and current.startswith(f"{task_id}#"):
        return None, None

    branches = _list_local_branches()
    if not branches:
        return None, "No local git branches found. Ensure the working directory is a git repo."

    numbered = "\n".join(f"{i + 1}. {b}" for i, b in enumerate(branches))
    result = await ctx.elicit(
        f"Select base branch to fork from:\n{numbered}\nSelect number:",
        _SelectBranch,
    )
    if result.action != "accept":
        return None, "Branch selection cancelled."
    idx = result.data.selection - 1
    if not (0 <= idx < len(branches)):
        return None, "Invalid branch selection."
    base_branch = branches[idx]

    description = await _generate_branch_description(ctx, task["name"], project["name"])
    branch_name = f"{task_id}#{description}"

    _create_task_branch(branch_name, base_branch)
    return branch_name, None


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


def _get_employee_id(client: Any, db: Any) -> int:
    """Return employee_id from cache or Odoo, caching on first fetch."""
    cached = db.get_setting("employee_id")
    if cached is not None:
        return int(cached)
    employee_id = get_employee_id(client, client.uid)
    db.set_setting("employee_id", str(employee_id))
    return employee_id


def _lookup_task_by_id(client: Any, task_id: int) -> Optional[tuple[dict, dict]]:
    """Look up a task directly by ID; return (task, project) or None if not found."""
    records = client.execute(
        "project.task",
        "search_read",
        [("id", "=", task_id)],
        fields=["id", "name", "project_id"],
        limit=1,
    )
    if not records:
        return None
    r = records[0]
    project_raw = r.get("project_id")
    project = {
        "id": project_raw[0] if isinstance(project_raw, (list, tuple)) else project_raw,
        "name": project_raw[1] if isinstance(project_raw, (list, tuple)) else str(project_raw),
    }
    return {"id": r["id"], "name": r["name"]}, project


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


def _build_session_result(
    session: Any,
    task: dict,
    project: dict,
    timesheet_id: int,
    *,
    branch_name: Optional[str] = None,
    warning: Optional[str] = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "session_id": session.id,
        "task_id": task["id"],
        "task_name": task["name"],
        "project_name": project["name"],
        "started_at": session.started_at.isoformat(),
        "timesheet_id": timesheet_id,
    }
    if branch_name is not None:
        result["branch_name"] = branch_name
    if warning is not None:
        result["warning"] = warning
    return result


class StartTaskCommand(Command):
    """Start time-tracking a project task, creating a placeholder timesheet entry."""

    _name = "start_task"
    _description = (
        "Begin tracking time on an Odoo project.task. When task_id is supplied, looks up "
        "the task directly and skips name-search disambiguation. Without task_id, searches "
        "by task_name_query and project_name_query with disambiguation prompts. Always asks "
        "for confirmation before starting. Creates a placeholder timesheet entry in Odoo."
    )

    async def execute(
        self,
        task_name_query: str,
        ctx: Any,
        project_name_query: Optional[str] = None,
        task_id: Optional[int] = None,
    ) -> dict[str, Any]:
        """Start a task tracking session.

        :param task_name_query: Task name substring to search for (used when task_id is absent or lookup fails).
        :param ctx: FastMCP Context for elicitation.
        :param project_name_query: Optional project name substring to narrow search.
        :param task_id: Optional Odoo task ID. When provided, looks up the task directly.
        :return: Session details including task name, project, and started_at.
        """
        assert_odoo_devcontainer()

        warning: Optional[str] = None
        task: Optional[dict] = None
        project: Optional[dict] = None

        if task_id is not None:
            found = _lookup_task_by_id(self._client, task_id)
            if found is not None:
                task, project = found
            elif task_name_query:
                warning = f"Task ID {task_id} not found; falling back to name search."
            else:
                return {"error": f"Task {task_id} not found."}

        if task is None:
            err: Optional[str]
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

        branch_name, branch_err = await _setup_task_branch(ctx, task, project)
        if branch_err:
            return {"error": branch_err}

        db = TaskStateDB()
        existing = db.get_active_session(task["id"])
        if existing is not None:
            return {
                "error": (
                    f"Task {task['name']!r} already has an active session "
                    f"(id={existing.id}, state={existing.state.value})."
                )
            }

        employee_id = _get_employee_id(self._client, db)

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

        return _build_session_result(
            session, task, project, timesheet_id,
            branch_name=branch_name,
            warning=warning,
        )
