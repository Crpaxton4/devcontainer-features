"""MCP ``start_task`` tool: interaction surface composing search + start commands.

This module owns all MCP-specific concerns for starting a task — argument
elicitation (project/task disambiguation, confirmation), git branch setup, and
optional AI branch-name generation via ``ctx.sample`` (gated on the client
advertising the ``sampling`` capability, with a deterministic slug fallback) —
and then delegates the actual Odoo/state mutation to the atomic
:class:`StartTaskCommand`. Commands never see
the FastMCP ``ctx``; primitives resolved here are passed to the command.
"""

import re
import subprocess
from typing import Any, Callable, Optional

from fastmcp import Context
from mcp.types import ClientCapabilities, SamplingCapability
from pydantic import BaseModel

from odoo_sdk.commands import Registry

from .composition import composition_tool


class _SelectIndex(BaseModel):
    """One-field schema for picking a numbered item (project, task, or branch)."""

    selection: int


class _ConfirmGate(BaseModel):
    """Fieldless schema for a pure confirmation.

    An empty object schema (no properties, nothing required) makes FastMCP
    render a single Accept/Decline with no form field — the accept/decline
    action itself is the answer. Unlike ``response_type=None`` this is not
    deprecated in FastMCP 3.4.x, so it emits no ``FastMCPDeprecationWarning``.
    """


def _git(*args: str) -> subprocess.CompletedProcess:
    """Run a read-only ``git`` command, capturing its text output."""
    return subprocess.run(["git", *args], capture_output=True, text=True)


def _current_branch() -> Optional[str]:
    result = _git("rev-parse", "--abbrev-ref", "HEAD")
    name = result.stdout.strip()
    return name if result.returncode == 0 and name != "HEAD" else None


def _list_local_branches() -> list[str]:
    result = _git("branch", "--format=%(refname:short)")
    if result.returncode != 0:
        return []
    branches = [b.strip() for b in result.stdout.splitlines() if b.strip() and "#" not in b]
    return sorted(branches, key=lambda b: (len(b), b))


def _is_dirty() -> bool:
    result = _git("status", "--porcelain")
    return result.returncode == 0 and bool(result.stdout.strip())


def _branch_exists(branch_name: str) -> bool:
    """True when ``branch_name`` already exists locally."""
    result = _git("rev-parse", "--verify", "--quiet", f"refs/heads/{branch_name}")
    return result.returncode == 0


def _stash_count() -> int:
    """Return the number of entries currently on the git stash (``0`` on error)."""
    result = _git("stash", "list")
    if result.returncode != 0:
        return 0
    return sum(1 for line in result.stdout.splitlines() if line.strip())


def _rollback_task_branch(branch_name: str, original_branch: Optional[str]) -> None:
    """Undo a branch created this run: switch back, then delete ``branch_name``.

    Best-effort cleanup when ``start_task`` fails downstream of branch setup
    (#164), so a raised command does not leave a dangling task branch checked
    out. Switching off ``branch_name`` first lets ``git branch -D`` succeed
    (git refuses to delete the current branch).

    :param branch_name: The task branch created this run, to be deleted.
    :type branch_name: str
    :param original_branch: Branch to return to; skipped when ``None`` (e.g. a
        prior detached HEAD).
    :type original_branch: Optional[str]
    """
    if original_branch is not None:
        _git("checkout", original_branch)
    _git("branch", "-D", branch_name)


def _create_task_branch(branch_name: str, base_branch: str) -> bool:
    """Create or switch to ``branch_name``, preserving any local changes.

    Idempotent (#149): when ``branch_name`` already exists it is checked out
    instead of re-created (``git checkout -b`` aborts with exit 128 otherwise).
    Stash-safe (#150): ``git stash push -u`` carries untracked files, and the
    matching ``pop`` runs only when an entry was actually pushed — a plain
    stash on an untracked-only tree saves nothing, so an unconditional ``pop``
    would fail with "No stash entries found".

    :param branch_name: Target branch to end up on.
    :type branch_name: str
    :param base_branch: Base branch to fork from when creating a new branch.
    :type base_branch: str
    :return: ``True`` when a new branch was created, ``False`` when an existing
        one was merely checked out — lets callers roll back only fresh branches.
    :rtype: bool
    """
    stashed = False
    if _is_dirty():
        before = _stash_count()
        subprocess.run(
            ["git", "stash", "push", "-u", "-m", f"auto-stash: {branch_name}"],
            check=True,
        )
        stashed = _stash_count() > before
    created = not _branch_exists(branch_name)
    try:
        if created:
            subprocess.run(["git", "checkout", "-b", branch_name, base_branch], check=True)
        else:
            subprocess.run(["git", "checkout", branch_name], check=True)
    finally:
        if stashed:
            subprocess.run(["git", "stash", "pop"], check=True)
    return created


def _slugify(text: str) -> str:
    """Lowercase, hyphenate, and trim ``text`` into a git-safe branch suffix."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:45]


def _client_supports_sampling(ctx: Any) -> bool:
    """True only when the MCP client advertised the ``sampling`` capability.

    Sampling (server->client LLM completion) is optional; clients like Claude
    Code do not advertise it. Probing here lets us degrade gracefully instead
    of hard-failing inside ``ctx.sample``.
    """
    try:
        return bool(
            ctx.session.check_client_capability(
                ClientCapabilities(sampling=SamplingCapability())
            )
        )
    except Exception:
        return False


async def _generate_branch_description(ctx: Any, task_name: str, project_name: str) -> str:
    """Return a branch-name suffix, preferring an LLM-sampled slug.

    Falls back to a deterministic slug derived from ``task_name`` whenever the
    client cannot sample or sampling yields nothing, so the ``start_task`` flow
    never hard-fails on this optional capability.
    """
    fallback = _slugify(task_name)
    if not _client_supports_sampling(ctx):
        return fallback
    try:
        response = await ctx.sample(
            f"Generate a git branch name suffix for this task.\n"
            f"Rules: lowercase only, hyphens instead of spaces/special chars, max 45 chars, no leading/trailing hyphens.\n"
            f"Output ONLY the suffix text, nothing else.\n"
            f"Task: {task_name}\nProject: {project_name}",
            max_tokens=30,
        )
    except Exception:
        return fallback
    return _slugify(response.text.strip()) or fallback


async def _setup_task_branch(
    ctx: Any, task: dict, project: dict
) -> tuple[Optional[str], bool, Optional[str]]:
    task_id = task["id"]
    current = _current_branch()
    if current and current.startswith(f"{task_id}#"):
        return None, False, None

    branches = _list_local_branches()
    if not branches:
        return None, False, "No local git branches found. Ensure the working directory is a git repo."

    numbered = "\n".join(f"{i + 1}. {b}" for i, b in enumerate(branches))
    result = await ctx.elicit(
        f"Select base branch to fork from:\n{numbered}\nSelect number:",
        _SelectIndex,
    )
    if result.action != "accept":
        return None, False, "Branch selection cancelled."
    idx = result.data.selection - 1
    if not (0 <= idx < len(branches)):
        return None, False, "Invalid branch selection."
    base_branch = branches[idx]

    description = await _generate_branch_description(ctx, task["name"], project["name"])
    branch_name = f"{task_id}#{description}"

    created = _create_task_branch(branch_name, base_branch)
    return branch_name, created, None


async def _disambiguate(
    ctx: Any,
    message: str,
    items: list,
    label: Callable[[Any], str] = lambda item: item["name"],
) -> Optional[Any]:
    """Prompt user to pick one item from a list; return item or None on cancel/bad index."""
    numbered = "\n".join(f"{i + 1}. {label(item)}" for i, item in enumerate(items))
    result = await ctx.elicit(f"{message}\n{numbered}\nSelect number:", _SelectIndex)
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


async def _search_and_pick(
    ctx: Any,
    results: list,
    *,
    empty_error: str,
    multi_prompt: str,
    cancel_error: str,
) -> tuple[Optional[dict], Optional[str]]:
    """Resolve a search result to one item; return (item, error) — one non-None.

    Empty results yield ``empty_error``; a single result is returned directly; a
    multiple result set is disambiguated, with a cancel/bad-index returning
    ``cancel_error``.
    """
    if not results:
        return None, empty_error
    if len(results) == 1:
        return results[0], None
    picked = await _disambiguate(ctx, multi_prompt, results)
    if picked is None:
        return None, cancel_error
    return picked, None


async def _resolve_project(
    ctx: Any, registry: Registry, query: str
) -> tuple[Optional[dict], Optional[str]]:
    """Return (project, error_string) — exactly one will be non-None."""
    projects = registry["search_projects"].execute(query, limit=10)
    return await _search_and_pick(
        ctx,
        projects,
        empty_error=f"No projects found matching {query!r}.",
        multi_prompt="Multiple projects found:",
        cancel_error="Project selection cancelled.",
    )


async def _resolve_task(
    ctx: Any, registry: Registry, query: str, project_id: int, project_name: str
) -> tuple[Optional[dict], Optional[str]]:
    """Return (task, error_string) — exactly one will be non-None."""
    tasks = registry["search_tasks"].execute(query, project_id, limit=10)
    return await _search_and_pick(
        ctx,
        tasks,
        empty_error=f"No tasks found matching {query!r} in project {project_name!r}.",
        multi_prompt="Multiple tasks found:",
        cancel_error="Task selection cancelled.",
    )


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


@composition_tool("start_task")
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
        before starting. Records a local tracking session and posts a chatter note;
        writes no Odoo timesheet (hours are derived by the sessionization upload).
        """
        task, project, warning, error = await _resolve_task_and_project(
            ctx, registry, task_name_query, project_name_query, task_id
        )
        if error is not None:
            return error

        confirm = await ctx.elicit(
            f"Start tracking time on task:\n  Task: {task['name']}\n"
            f"  Project: {project['name']}\n\nConfirm?",
            _ConfirmGate,
        )
        if confirm.action != "accept":
            return {"error": "Task start cancelled."}

        original_branch = _current_branch()
        branch_name, branch_created, branch_err = await _setup_task_branch(ctx, task, project)
        if branch_err:
            return {"error": branch_err}

        try:
            return registry["start_task"].execute(
                task_id=task["id"],
                task_name=task["name"],
                project_id=project["id"],
                project_name=project["name"],
                branch_name=branch_name,
                warning=warning,
            )
        except Exception:
            # Raise-based error contract (#223): the start command raises on
            # failure (e.g. an active session -> ``TaskAlreadyRunningError``, or
            # an Odoo fault -> ``OdooError``). This flow needs cleanup before the
            # failure surfaces, so it catches to roll back only a branch freshly
            # created this run (#164), then re-raises the *original typed*
            # exception unchanged for the MCP ``_error_boundary`` (#222) to format
            # — it is not swallowed into an ``{"error": ...}`` dict here.
            if branch_created and branch_name is not None:
                _rollback_task_branch(branch_name, original_branch)
            raise

    return start_task
