"""MCP ``start_task`` tool: interaction surface composing search + start commands.

This module owns all MCP-specific concerns for starting a task — argument
elicitation (project/task disambiguation, confirmation), git branch setup, and
AI branch-name generation via ``ctx.sample`` — and then delegates the actual
Odoo/state mutation to the atomic :class:`StartTaskCommand`. Commands never see
the FastMCP ``ctx``; primitives resolved here are passed to the command.
"""

import re
import subprocess
from typing import Any, Optional

from fastmcp import Context
from pydantic import BaseModel

from odoo_sdk.commands import Registry


class _SelectProject(BaseModel):
    selection: int


class _SelectTask(BaseModel):
    selection: int


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


async def _resolve_project(
    ctx: Any, registry: Registry, query: str
) -> tuple[Optional[dict], Optional[str]]:
    """Return (project, error_string) — exactly one will be non-None."""
    projects = registry["search_projects"].execute(query, limit=10)
    if not projects:
        return None, f"No projects found matching {query!r}."
    if len(projects) == 1:
        return projects[0], None
    project = await _disambiguate(ctx, "Multiple projects found:", projects, _SelectProject)
    if project is None:
        return None, "Project selection cancelled."
    return project, None


async def _resolve_task(
    ctx: Any, registry: Registry, query: str, project_id: int, project_name: str
) -> tuple[Optional[dict], Optional[str]]:
    """Return (task, error_string) — exactly one will be non-None."""
    tasks = registry["search_tasks"].execute(query, project_id, limit=10)
    if not tasks:
        return None, f"No tasks found matching {query!r} in project {project_name!r}."
    if len(tasks) == 1:
        return tasks[0], None
    task = await _disambiguate(ctx, "Multiple tasks found:", tasks, _SelectTask)
    if task is None:
        return None, "Task selection cancelled."
    return task, None


async def _resolve_task_and_project(
    ctx: Any,
    registry: Registry,
    task_name_query: str,
    project_name_query: Optional[str],
    task_id: Optional[int],
) -> tuple[Optional[dict], Optional[dict], Optional[str], Optional[dict]]:
    """Resolve (task, project, warning, error) from ids or name search."""
    client = registry["search_projects"]._client
    if task_id is not None:
        found = _lookup_task_by_id(client, task_id)
        if found is not None:
            task, project = found
            return task, project, None, None
        if not task_name_query:
            return None, None, None, {"error": f"Task {task_id} not found."}
        warning = f"Task ID {task_id} not found; falling back to name search."
    else:
        warning = None

    project, err = await _resolve_project(ctx, registry, project_name_query or "")
    if err:
        return None, None, None, {"error": err}
    task, err = await _resolve_task(
        ctx, registry, task_name_query, project["id"], project["name"]
    )
    if err:
        return None, None, None, {"error": err}
    return task, project, warning, None


def make_start_task_tool(registry: Registry):
    """Build the async ``start_task`` MCP tool bound to ``registry``.

    :param registry: Command registry providing search + start commands.
    :type registry: Registry
    :return: Async callable implementing the ``start_task`` tool.
    """

    async def start_task(
        task_name_query: str,
        ctx: Context,
        project_name_query: Optional[str] = None,
        task_id: Optional[int] = None,
    ) -> dict[str, Any]:
        """Begin tracking time on an Odoo project.task.

        When task_id is supplied, looks up the task directly and skips name-search
        disambiguation. Without task_id, searches by task_name_query and
        project_name_query with disambiguation prompts. Always asks for confirmation
        before starting. Creates a placeholder timesheet entry in Odoo.
        """
        task, project, warning, error = await _resolve_task_and_project(
            ctx, registry, task_name_query, project_name_query, task_id
        )
        if error is not None:
            return error

        confirm = await ctx.elicit(
            f"Start tracking time on task:\n  Task: {task['name']}\n"
            f"  Project: {project['name']}\n\nConfirm?",
            response_type=None,
        )
        if confirm.action != "accept":
            return {"error": "Task start cancelled."}

        branch_name, branch_err = await _setup_task_branch(ctx, task, project)
        if branch_err:
            return {"error": branch_err}

        return registry["start_task"].execute(
            task_id=task["id"],
            task_name=task["name"],
            project_id=project["id"],
            project_name=project["name"],
            branch_name=branch_name,
            warning=warning,
        )

    return start_task
