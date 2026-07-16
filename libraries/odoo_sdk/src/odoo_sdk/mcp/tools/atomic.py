"""Explicit MCP tool definitions for the atomic (non-``ctx``) commands.

Each factory returns a plainly-written function with a real, typed signature that
delegates to the like-named command in the registry. Defining these explicitly —
rather than reflecting ``command.execute`` via ``inspect.signature`` — keeps the
MCP wire schema an intentional, reviewable part of the interaction surface.
"""

from typing import Any, Callable, Dict, List, Optional, Tuple

from odoo_sdk.commands import Registry

#: Public MCP tool name -> factory ``(registry) -> tool callable`` for the atomic
#: (non-``ctx``) commands. Populated at import time by :func:`atomic_tool`,
#: replacing the formerly hand-maintained dict literal.
ATOMIC_TOOL_FACTORIES: Dict[str, Callable[[Registry], Callable[..., Any]]] = {}

_Factory = Callable[[Registry], Callable[..., Any]]


def atomic_tool(name: str) -> Callable[[_Factory], _Factory]:
    """Register the decorated factory in :data:`ATOMIC_TOOL_FACTORIES`.

    Apply this to every atomic tool factory. ``name`` is the *public MCP tool
    name*; it is a separate argument from the command name the factory body
    looks up (``registry["..."]``), so a tool may be exposed under a name that
    differs from its backing command. The factory itself is returned unchanged.

    :param name: Public tool name under which to register the factory.
    :type name: str
    :raises ValueError: If ``name`` is already registered to another factory,
        which would silently drop one tool from the atomic surface.
    :return: A decorator that registers and returns the factory.
    :rtype: Callable[[_Factory], _Factory]
    """

    def register(factory: _Factory) -> _Factory:
        if name in ATOMIC_TOOL_FACTORIES:
            raise ValueError(
                f"Duplicate atomic tool name {name!r}: "
                f"{ATOMIC_TOOL_FACTORIES[name].__name__} is already registered."
            )
        ATOMIC_TOOL_FACTORIES[name] = factory
        return factory

    return register


@atomic_tool("get_uid")
def make_get_uid_tool(registry: Registry):
    def get_uid() -> int:
        """Get the UID of the current user."""
        return registry["get_uid"].execute()

    return get_uid


@atomic_tool("get_models")
def make_get_models_tool(registry: Registry):
    def get_models() -> List[Dict[str, Any]]:
        """Get a list of all models with their names."""
        return registry["get_models"].execute()

    return get_models


@atomic_tool("get_tasks")
def make_get_tasks_tool(registry: Registry):
    def get_tasks(
        domain: Optional[List[Tuple[str, str, Any]]] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """List project tasks with an optional Odoo domain filter."""
        return registry["get_tasks"].execute(domain=domain, limit=limit)

    return get_tasks


@atomic_tool("get_todo")
def make_get_todo_tool(registry: Registry):
    def get_todo(task_id: int) -> Optional[Dict[str, Any]]:
        """Return one project task by id, or None if not found."""
        return registry["get_todo"].execute(task_id)

    return get_todo


@atomic_tool("get_task")
def make_get_task_tool(registry: Registry):
    def get_task(
        task_id: int, include: Optional[List[str]] = None
    ) -> Optional[dict]:
        """Fetch task context for a project task with opt-in extra detail."""
        return registry["get_task"].execute(task_id, include=include)

    return get_task


@atomic_tool("get_task_chatter")
def make_get_task_chatter_tool(registry: Registry):
    def get_task_chatter(task_id: int, limit: int = 100) -> List[dict]:
        """Fetch all chatter messages for a task, sorted oldest-first."""
        return registry["get_task_chatter"].execute(task_id, limit=limit)

    return get_task_chatter


@atomic_tool("get_mail_status")
def make_get_mail_status_tool(registry: Registry):
    def get_mail_status(res_model: str, res_id: int) -> List[dict]:
        """Report outgoing-mail (``mail.mail``) delivery status for a record."""
        return registry["get_mail_status"].execute(res_model, res_id)

    return get_mail_status


@atomic_tool("get_task_attachments")
def make_get_task_attachments_tool(registry: Registry):
    def get_task_attachments(
        task_id: int, include_content: bool = False
    ) -> List[dict]:
        """List a task's attachments from both the task and its chatter."""
        return registry["get_task_attachments"].execute(
            task_id, include_content=include_content
        )

    return get_task_attachments


@atomic_tool("read_attachment")
def make_read_attachment_tool(registry: Registry):
    def read_attachment(attachment_id: int, mode: str = "text") -> Dict[str, Any]:
        """Read one document already stored in Odoo (read-only)."""
        return registry["read_attachment"].execute(attachment_id, mode=mode)

    return read_attachment


@atomic_tool("create_task")
def make_create_task_tool(registry: Registry):
    def create_task(name: str, project_id: int, description: str = "") -> int:
        """Create a project task with standard default values."""
        return registry["create_task"].execute(name, project_id, description)

    return create_task


@atomic_tool("search_chatter")
def make_search_chatter_tool(registry: Registry):
    def search_chatter(
        query: str,
        model: Optional[str] = None,
        record_id: Optional[int] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Full-text search across Odoo chatter (``mail.message``) bodies."""
        return registry["search_chatter"].execute(
            query,
            model=model,
            record_id=record_id,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
        )

    return search_chatter


@atomic_tool("search_knowledge_articles")
def make_search_knowledge_articles_tool(registry: Registry):
    def search_knowledge_articles(
        query: str, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Search the Odoo Knowledge base (``knowledge.article``) by text."""
        return registry["search_knowledge_articles"].execute(query, limit=limit)

    return search_knowledge_articles


@atomic_tool("read_knowledge_article")
def make_read_knowledge_article_tool(registry: Registry):
    def read_knowledge_article(article_id: int) -> Dict[str, Any]:
        """Read one Odoo Knowledge article (``knowledge.article``) by id."""
        return registry["read_knowledge_article"].execute(article_id)

    return read_knowledge_article


@atomic_tool("search_projects")
def make_search_projects_tool(registry: Registry):
    def search_projects(query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Search Odoo projects by name substring; returns id/name candidates."""
        return registry["search_projects"].execute(query, limit=limit)

    return search_projects


@atomic_tool("search_tasks")
def make_search_tasks_tool(registry: Registry):
    def search_tasks(
        query: str, project_id: int, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Search tasks within a project by name; returns id/name candidates."""
        return registry["search_tasks"].execute(query, project_id, limit=limit)

    return search_tasks


@atomic_tool("resume_task")
def make_resume_task_tool(registry: Registry):
    def resume_task(task_id: int) -> Dict[str, Any]:
        """Resume an AWAITING_ANSWERS session, transitioning it back to RUNNING."""
        return registry["resume_task"].execute(task_id)

    return resume_task


@atomic_tool("abort_task")
def make_abort_task_tool(registry: Registry):
    def abort_task(task_id: int) -> Dict[str, Any]:
        """Force-close a wedged session without logging hours; retire its anchor."""
        return registry["abort_task"].execute(task_id)

    return abort_task


@atomic_tool("assign_event")
def make_assign_event_tool(registry: Registry):
    def assign_event(event_ids: List[int], task_id: int) -> Dict[str, Any]:
        """Attribute tracker events to an Odoo task id in one transaction."""
        return registry["assign_event"].execute(event_ids=event_ids, task_id=task_id)

    return assign_event


@atomic_tool("discover_runs")
def make_discover_runs_tool(registry: Registry):
    def discover_runs(stale_after_hours: float = 12.0) -> List[Dict[str, Any]]:
        """Discover active runs in the central tracker DB."""
        return registry["discover_runs"].execute(stale_after_hours=stale_after_hours)

    return discover_runs


@atomic_tool("abort_run")
def make_abort_run_tool(registry: Registry):
    def abort_run(run_id_or_task_id: int) -> Dict[str, Any]:
        """Abort a stale run in the central tracker DB and close its Odoo anchor."""
        return registry["abort_run"].execute(run_id_or_task_id)

    return abort_run


@atomic_tool("resync")
def make_resync_tool(registry: Registry):
    def resync(sources: str = "git,github,odoo") -> Dict[str, Any]:
        """Reconcile local event state against git, GitHub, and Odoo chatter."""
        return registry["resync"].execute(sources=sources)

    return resync


@atomic_tool("task_status")
def make_task_status_tool(registry: Registry):
    def task_status() -> List[Dict[str, Any]]:
        """Show all actively tracked tasks with elapsed time."""
        return registry["task_status"].execute()

    return task_status


@atomic_tool("task_note")
def make_task_note_tool(registry: Registry):
    def task_note(task_id: int, note: str) -> Dict[str, Any]:
        """Post a free-form note to the task chatter and the local session log."""
        return registry["task_note"].execute(task_id, note)

    return task_note


@atomic_tool("task_list")
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


@atomic_tool("task_aging")
def make_task_aging_tool(registry: Registry):
    def task_aging(
        project_id: Optional[int] = None,
        stage: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """List open project tasks going stale, sorted stalest-first (read-only)."""
        return registry["task_aging"].execute(
            project_id=project_id, stage=stage, limit=limit
        )

    return task_aging


@atomic_tool("task_question")
def make_task_question_tool(registry: Registry):
    def task_question(task_id: int, question: str) -> Dict[str, Any]:
        """Post a question to the task chatter; transitions to AWAITING_ANSWERS."""
        return registry["task_question"].execute(task_id, question)

    return task_question


@atomic_tool("optimize_sessions")
def make_optimize_sessions_tool(registry: Registry):
    def optimize_sessions(
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        sweep_min_gap_mins: Optional[int] = None,
        sweep_max_gap_mins: Optional[int] = None,
        sweep_step_mins: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Analyze stored events by sweeping gaps; report the best gap (read-only)."""
        return registry["optimize_sessions"].execute(
            start_date=start_date,
            end_date=end_date,
            sweep_min_gap_mins=sweep_min_gap_mins,
            sweep_max_gap_mins=sweep_max_gap_mins,
            sweep_step_mins=sweep_step_mins,
        )

    return optimize_sessions


@atomic_tool("unbilled_hours")
def make_unbilled_hours_tool(registry: Registry):
    def unbilled_hours(
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        project_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Report logged-but-not-yet-invoiced timesheet hours (read-only)."""
        return registry["unbilled_hours"].execute(
            start_date=start_date, end_date=end_date, project_id=project_id
        )

    return unbilled_hours


@atomic_tool("unlogged_time_report")
def make_unlogged_time_report_tool(registry: Registry):
    def unlogged_time_report(
        start_date: str,
        end_date: str,
        only_mine: bool = True,
        include_all: bool = False,
    ) -> Dict[str, Any]:
        """Reconcile derived-vs-logged hours per day and task (read-only)."""
        return registry["unlogged_time_report"].execute(
            start_date,
            end_date,
            only_mine=only_mine,
            include_all=include_all,
        )

    return unlogged_time_report


@atomic_tool("list_runs")
def make_list_runs_tool(registry: Registry):
    def list_runs() -> List[Dict[str, Any]]:
        """List the active tracker runs with elapsed time."""
        return registry["list_runs"].execute()

    return list_runs


@atomic_tool("report_runs")
def make_report_runs_tool(registry: Registry):
    def report_runs(include_stopped: bool = False) -> List[Dict[str, Any]]:
        """Report tracker runs, optionally including the stopped ones."""
        return registry["report_runs"].execute(include_stopped=include_stopped)

    return report_runs


@atomic_tool("stop_run")
def make_stop_run_tool(registry: Registry):
    def stop_run(run_id: int) -> Dict[str, Any]:
        """Force-stop one tracker run by its SQLite run id."""
        return registry["stop_run"].execute(run_id)

    return stop_run


@atomic_tool("stop_all")
def make_stop_all_tool(registry: Registry):
    def stop_all() -> List[Dict[str, Any]]:
        """Force-stop every active tracker run."""
        return registry["stop_all"].execute()

    return stop_all


@atomic_tool("normalize_timesheets")
def make_normalize_timesheets_tool(registry: Registry):
    def normalize_timesheets(apply: bool = False) -> Dict[str, Any]:
        """Detect (and optionally merge) duplicate timesheet entries."""
        return registry["normalize_timesheets"].execute(apply=apply)

    return normalize_timesheets


@atomic_tool("query_sessions")
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


@atomic_tool("timesheet_summary")
def make_timesheet_summary_tool(registry: Registry):
    def timesheet_summary(
        start_date: str,
        end_date: str,
        group_by: str = "project",
        only_mine: bool = True,
    ) -> Dict[str, Any]:
        """Summarize logged timesheet hours over a date range, grouped one way."""
        return registry["timesheet_summary"].execute(
            start_date,
            end_date,
            group_by=group_by,
            only_mine=only_mine,
        )

    return timesheet_summary

