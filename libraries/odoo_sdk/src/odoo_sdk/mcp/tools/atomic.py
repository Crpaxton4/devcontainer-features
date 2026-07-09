"""Explicit MCP tool definitions for the atomic (non-``ctx``) commands.

Each factory returns a plainly-written function with a real, typed signature that
delegates to the like-named command in the registry. Defining these explicitly —
rather than reflecting ``command.execute`` via ``inspect.signature`` — keeps the
MCP wire schema an intentional, reviewable part of the interaction surface.
"""

from typing import Any, Dict, List, Optional, Tuple

from odoo_sdk.commands import Registry


def make_get_uid_tool(registry: Registry):
    def get_uid() -> int:
        """Get the UID of the current user."""
        return registry["get_uid"].execute()

    return get_uid


def make_get_models_tool(registry: Registry):
    def get_models() -> List[Dict[str, Any]]:
        """Get a list of all models with their names."""
        return registry["get_models"].execute()

    return get_models


def make_get_tasks_tool(registry: Registry):
    def get_tasks(
        domain: Optional[List[Tuple[str, str, Any]]] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """List project tasks with an optional Odoo domain filter."""
        return registry["get_tasks"].execute(domain=domain, limit=limit)

    return get_tasks


def make_get_todo_tool(registry: Registry):
    def get_todo(task_id: int) -> Optional[Dict[str, Any]]:
        """Return one project task by id, or None if not found."""
        return registry["get_todo"].execute(task_id)

    return get_todo


def make_get_task_tool(registry: Registry):
    def get_task(
        task_id: int, include: Optional[List[str]] = None
    ) -> Optional[dict]:
        """Fetch task context for a project task with opt-in extra detail.

        Base identity fields (name, project, stage, assignees, deadline,
        priority, tags) are always returned. ``include`` selects extra, more
        expensive detail; each entry is one of: ``description``, ``chatter``,
        ``dependencies``, ``timesheets``, ``subtasks``. When omitted the cheap
        default is description only.
        """
        return registry["get_task"].execute(task_id, include=include)

    return get_task


def make_get_task_chatter_tool(registry: Registry):
    def get_task_chatter(task_id: int, limit: int = 100) -> List[dict]:
        """Fetch all chatter messages for a task, sorted oldest-first."""
        return registry["get_task_chatter"].execute(task_id, limit=limit)

    return get_task_chatter


def make_create_task_tool(registry: Registry):
    def create_task(name: str, project_id: int, description: str = "") -> int:
        """Create a project task with standard default values."""
        return registry["create_task"].execute(name, project_id, description)

    return create_task


def make_search_projects_tool(registry: Registry):
    def search_projects(query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Search Odoo projects by name substring; returns id/name candidates."""
        return registry["search_projects"].execute(query, limit=limit)

    return search_projects


def make_search_tasks_tool(registry: Registry):
    def search_tasks(
        query: str, project_id: int, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Search tasks within a project by name; returns id/name candidates."""
        return registry["search_tasks"].execute(query, project_id, limit=limit)

    return search_tasks


def make_resume_task_tool(registry: Registry):
    def resume_task(task_id: int) -> Dict[str, Any]:
        """Resume an AWAITING_ANSWERS session, transitioning it back to RUNNING."""
        return registry["resume_task"].execute(task_id)

    return resume_task


def make_task_status_tool(registry: Registry):
    def task_status() -> List[Dict[str, Any]]:
        """Show all actively tracked tasks with elapsed time."""
        return registry["task_status"].execute()

    return task_status


def make_task_note_tool(registry: Registry):
    def task_note(task_id: int, note: str) -> Dict[str, Any]:
        """Post a free-form note to the task chatter and the local session log."""
        return registry["task_note"].execute(task_id, note)

    return task_note


def make_task_list_tool(registry: Registry):
    def task_list(
        project_name_query: Optional[str] = None,
        stage: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """List project tasks assigned to the authenticated user."""
        return registry["task_list"].execute(
            project_name_query=project_name_query, stage=stage, limit=limit
        )

    return task_list


def make_task_question_tool(registry: Registry):
    def task_question(task_id: int, question: str) -> Dict[str, Any]:
        """Post a question to the task chatter; transitions to AWAITING_ANSWERS."""
        return registry["task_question"].execute(task_id, question)

    return task_question


def make_optimize_sessions_tool(registry: Registry):
    def optimize_sessions(
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        sweep_min_gap_mins: Optional[int] = None,
        sweep_max_gap_mins: Optional[int] = None,
        sweep_step_mins: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Analyze stored events by sweeping gaps; report the best gap (read-only)."""
        overrides = {
            "sweep_min_gap_mins": sweep_min_gap_mins,
            "sweep_max_gap_mins": sweep_max_gap_mins,
            "sweep_step_mins": sweep_step_mins,
        }
        return registry["optimize_sessions"].execute(
            start_date=start_date,
            end_date=end_date,
            **overrides,
        )

    return optimize_sessions


def make_ingest_sessions_tool(registry: Registry):
    def ingest_sessions(
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Ingest stored events into global cross-day sessions incrementally."""
        return registry["ingest_sessions"].execute(
            start_date=start_date, end_date=end_date
        )

    return ingest_sessions


def make_query_sessions_tool(registry: Registry):
    def query_sessions(
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        task_id: Optional[str] = None,
        repo: Optional[str] = None,
        strategy_name: Optional[str] = None,
        include_events: bool = True,
    ) -> List[Dict[str, Any]]:
        """Query global cross-day sessions overlapping a date range (whole)."""
        return registry["query_sessions"].execute(
            start_date=start_date,
            end_date=end_date,
            task_id=task_id,
            repo=repo,
            strategy_name=strategy_name,
            include_events=include_events,
        )

    return query_sessions


# Tool name -> factory(registry) -> tool callable, for the atomic commands.
ATOMIC_TOOL_FACTORIES = {
    "get_uid": make_get_uid_tool,
    "get_models": make_get_models_tool,
    "get_tasks": make_get_tasks_tool,
    "get_todo": make_get_todo_tool,
    "get_task": make_get_task_tool,
    "get_task_chatter": make_get_task_chatter_tool,
    "create_task": make_create_task_tool,
    "search_projects": make_search_projects_tool,
    "search_tasks": make_search_tasks_tool,
    "resume_task": make_resume_task_tool,
    "task_status": make_task_status_tool,
    "task_note": make_task_note_tool,
    "task_list": make_task_list_tool,
    "task_question": make_task_question_tool,
    "optimize_sessions": make_optimize_sessions_tool,
    "ingest_sessions": make_ingest_sessions_tool,
    "query_sessions": make_query_sessions_tool,
}
