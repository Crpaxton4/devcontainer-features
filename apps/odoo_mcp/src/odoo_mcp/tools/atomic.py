"""Explicit MCP tool definitions for the atomic (non-``ctx``) commands.

Each factory returns a plainly-written function with a real, typed signature that
delegates to the like-named command of the command service. Defining these explicitly —
rather than reflecting ``command.execute`` via ``inspect.signature`` — keeps the
MCP wire schema an intentional, reviewable part of the interaction surface.
"""

from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from odoo_dev_core import CommandService

#: Public MCP tool name -> factory ``(service) -> tool callable`` for the atomic
#: (non-``ctx``) commands. Populated at import time by :func:`atomic_tool`,
#: replacing the formerly hand-maintained dict literal.
ATOMIC_TOOL_FACTORIES: Dict[str, Callable[[CommandService], Callable[..., Any]]] = {}

_Factory = Callable[[CommandService], Callable[..., Any]]


def atomic_tool(name: str) -> Callable[[_Factory], _Factory]:
    """Register the decorated factory in :data:`ATOMIC_TOOL_FACTORIES`.

    Apply this to every atomic tool factory. ``name`` is the *public MCP tool
    name*; it is a separate argument from the command name the factory body
    executes (``service.execute("...")``), so a tool may be exposed under a name that
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
def make_get_uid_tool(service: CommandService):
    def get_uid() -> int:
        """Get the UID of the current user."""
        return service.execute("get_uid")

    return get_uid


@atomic_tool("get_models")
def make_get_models_tool(service: CommandService):
    def get_models(persist: Optional[List[str]] = None) -> Dict[str, Any]:
        """Get a list of all models with their names, as
        ``{"models": [...], "persisted": {}}``. ``persist`` names models
        (e.g. ``["project.task"]``) whose resolved ir.model id is also written
        into the ``[model_ids]`` section of the SDK config file — the map
        schedule_activity reads — so the entry need not be typed by hand. When
        the config cannot be written the read still succeeds and ``warning``
        says why."""
        return service.execute("get_models", persist=persist)

    return get_models


@atomic_tool("get_tasks")
def make_get_tasks_tool(service: CommandService):
    def get_tasks(
        domain: Optional[List[Tuple[str, str, Any]]] = None,
        limit: int = 10,
        include: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """List project tasks with an optional Odoo domain filter and
        opt-in per-task detail (``include``, same contract as get_task)."""
        return service.execute("get_tasks", domain=domain, limit=limit, include=include)

    return get_tasks


@atomic_tool("get_todo")
def make_get_todo_tool(service: CommandService):
    def get_todo(task_id: int) -> Optional[Dict[str, Any]]:
        """Return one project task by id, or None if not found."""
        return service.execute("get_todo", task_id=task_id)

    return get_todo


@atomic_tool("get_task")
def make_get_task_tool(service: CommandService):
    def get_task(task_id: int, include: Optional[List[str]] = None) -> Optional[dict]:
        """Fetch task context for a project task with opt-in extra detail."""
        return service.execute("get_task", task_id=task_id, include=include)

    return get_task


@atomic_tool("get_task_chatter")
def make_get_task_chatter_tool(service: CommandService):
    def get_task_chatter(
        task_id: int,
        limit: int = 100,
        since: Optional[Union[int, str]] = None,
    ) -> List[dict]:
        """Fetch a task's newest chatter messages, returned oldest-first.

        Over-limit chatters keep the NEWEST messages. ``since`` fetches only
        messages after a cursor: an int is a message-id cursor, a string an
        Odoo datetime cursor.
        """
        return service.execute(
            "get_task_chatter", task_id=task_id, limit=limit, since=since
        )

    return get_task_chatter


@atomic_tool("get_mail_status")
def make_get_mail_status_tool(service: CommandService):
    def get_mail_status(res_model: str, res_id: int) -> List[dict]:
        """Report outgoing-mail (``mail.mail``) delivery status for a record."""
        return service.execute("get_mail_status", res_model=res_model, res_id=res_id)

    return get_mail_status


@atomic_tool("get_task_attachments")
def make_get_task_attachments_tool(service: CommandService):
    def get_task_attachments(task_id: int, include_content: bool = False) -> List[dict]:
        """List a task's attachments from both the task and its chatter."""
        return service.execute(
            "get_task_attachments", task_id=task_id, include_content=include_content
        )

    return get_task_attachments


@atomic_tool("read_attachment")
def make_read_attachment_tool(service: CommandService):
    def read_attachment(attachment_id: int, mode: str = "text") -> Dict[str, Any]:
        """Read one document already stored in Odoo (read-only)."""
        return service.execute(
            "read_attachment", attachment_id=attachment_id, mode=mode
        )

    return read_attachment


@atomic_tool("create_task")
def make_create_task_tool(service: CommandService):
    def create_task(name: str, project_id: int, description: str = "") -> int:
        """Create a project task with standard default values."""
        return service.execute(
            "create_task", name=name, project_id=project_id, description=description
        )

    return create_task


@atomic_tool("schedule_activity")
def make_schedule_activity_tool(service: CommandService):
    def schedule_activity(
        res_id: int,
        res_model: str = "project.task",
        activity_type: Optional[Union[int, str]] = None,
        summary: str = "",
        note: str = "",
        date_deadline: Optional[str] = None,
        user_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Schedule an Odoo activity (``mail.activity``) on a record.

        ``res_model`` defaults to ``project.task``. ``activity_type`` takes an
        id or a name (e.g. ``"Call"``); ``note`` is Markdown, ``date_deadline``
        an inclusive ``YYYY-MM-DD`` date, and ``user_id`` defaults to the
        authenticated user.
        """
        return service.execute(
            "schedule_activity",
            res_id=res_id,
            res_model=res_model,
            activity_type=activity_type,
            summary=summary,
            note=note,
            date_deadline=date_deadline,
            user_id=user_id,
        )

    return schedule_activity


@atomic_tool("get_activities")
def make_get_activities_tool(service: CommandService):
    def get_activities(
        res_id: Optional[int] = None,
        res_model: Optional[str] = None,
        user_id: Optional[int] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """List open activities on a record and/or for a user (read-only).

        A ``res_id`` without ``res_model`` is read as a ``project.task`` id; a
        call with no filters at all scopes to the authenticated user.
        """
        return service.execute(
            "get_activities",
            res_id=res_id,
            res_model=res_model,
            user_id=user_id,
            limit=limit,
        )

    return get_activities


@atomic_tool("mark_activity_done")
def make_mark_activity_done_tool(service: CommandService):
    def mark_activity_done(activity_id: int, feedback: str = "") -> Dict[str, Any]:
        """Mark an activity done (``action_feedback``), posting the feedback.

        Completing an activity deletes the ``mail.activity`` record, so the
        activity that was closed is returned in full.
        """
        return service.execute(
            "mark_activity_done", activity_id=activity_id, feedback=feedback
        )

    return mark_activity_done


@atomic_tool("search_activity_types")
def make_search_activity_types_tool(service: CommandService):
    def search_activity_types(
        query: Optional[str] = None,
        res_model: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """List activity types (``mail.activity.type``) available to schedule."""
        return service.execute(
            "search_activity_types", query=query, res_model=res_model, limit=limit
        )

    return search_activity_types


@atomic_tool("search_chatter")
def make_search_chatter_tool(service: CommandService):
    def search_chatter(
        query: str,
        model: Optional[str] = None,
        record_id: Optional[int] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Full-text search across Odoo chatter (``mail.message``) bodies."""
        return service.execute(
            "search_chatter",
            query=query,
            model=model,
            record_id=record_id,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
        )

    return search_chatter


@atomic_tool("search_knowledge_articles")
def make_search_knowledge_articles_tool(service: CommandService):
    def search_knowledge_articles(query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Search the Odoo Knowledge base (``knowledge.article``) by text."""
        return service.execute("search_knowledge_articles", query=query, limit=limit)

    return search_knowledge_articles


@atomic_tool("read_knowledge_article")
def make_read_knowledge_article_tool(service: CommandService):
    def read_knowledge_article(article_id: int) -> Dict[str, Any]:
        """Read one Odoo Knowledge article (``knowledge.article``) by id."""
        return service.execute("read_knowledge_article", article_id=article_id)

    return read_knowledge_article


@atomic_tool("search_projects")
def make_search_projects_tool(service: CommandService):
    def search_projects(query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Search Odoo projects by name substring; returns id/name candidates."""
        return service.execute("search_projects", query=query, limit=limit)

    return search_projects


@atomic_tool("search_tasks")
def make_search_tasks_tool(service: CommandService):
    def search_tasks(
        query: str, project_id: int, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Search tasks within a project by name; returns id/name candidates."""
        return service.execute(
            "search_tasks", query=query, project_id=project_id, limit=limit
        )

    return search_tasks


@atomic_tool("assign_event")
def make_assign_event_tool(service: CommandService):
    def assign_event(event_ids: List[int], task_id: int) -> Dict[str, Any]:
        """Attribute tracker events to an Odoo task id in one transaction."""
        return service.execute("assign_event", event_ids=event_ids, task_id=task_id)

    return assign_event


@atomic_tool("resync")
def make_resync_tool(service: CommandService):
    def resync(
        sources: str = "git,github,odoo",
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Reconcile local event state against git, GitHub, and Odoo chatter.

        Directory-agnostic: git discovers every repo under the cwd and github
        searches account-wide. Optional inclusive ``start``/``end`` ISO dates
        bound the capture window.
        """
        return service.execute("resync", sources=sources, start=start, end=end)

    return resync


@atomic_tool("task_note")
def make_task_note_tool(service: CommandService):
    def task_note(
        task_id: int,
        note: str,
        attachments: Optional[List[Dict[str, Any]]] = None,
        dedupe_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Post a note (max 500 chars) to the task chatter, optionally with files.

        Each attachment spec is ``{"path": <local file>}`` or
        ``{"content": <base64>, "name": <filename>}`` plus optional
        ``"mimetype"``. ``dedupe_key`` makes the call idempotent: a retried
        call with an already-seen key skips the post and returns the existing
        message id.

        Post ONE consolidated note per run just before stopping — every posted
        note notifies every follower on the task.

        Task chatter is CLIENT-VISIBLE, and no tool removes a note or unlinks
        an attachment once posted. Attach only deliverables the client asked
        to receive. Do NOT attach internal engineering material — scripts,
        logs, test or benchmark output, tracebacks, machine paths, or working
        analysis. Detail that will not fit the 500-char note belongs in the
        pull request, the commit history, or an internal channel; reference it
        from the note rather than attaching it.
        """
        return service.execute(
            "task_note",
            task_id=task_id,
            note=note,
            attachments=attachments,
            dedupe_key=dedupe_key,
        )

    return task_note


@atomic_tool("task_list")
def make_task_list_tool(service: CommandService):
    def task_list(
        project_name_query: Optional[str] = None,
        stage: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """List project tasks assigned to the authenticated user."""
        return service.execute(
            "task_list", project_name_query=project_name_query, stage=stage, limit=limit
        )

    return task_list


@atomic_tool("task_aging")
def make_task_aging_tool(service: CommandService):
    def task_aging(
        project_id: Optional[int] = None,
        stage: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """List open project tasks going stale, sorted stalest-first (read-only)."""
        return service.execute(
            "task_aging", project_id=project_id, stage=stage, limit=limit
        )

    return task_aging


@atomic_tool("task_question")
def make_task_question_tool(service: CommandService):
    def task_question(
        task_id: int, question: str, dedupe_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """Post a question (max 500 chars) to the task chatter.

        ``dedupe_key`` makes the call idempotent: a retried call with an
        already-seen key skips the post and returns the existing message id.
        """
        return service.execute(
            "task_question", task_id=task_id, question=question, dedupe_key=dedupe_key
        )

    return task_question


@atomic_tool("optimize_sessions")
def make_optimize_sessions_tool(service: CommandService):
    def optimize_sessions(
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        sweep_min_gap_mins: Optional[int] = None,
        sweep_max_gap_mins: Optional[int] = None,
        sweep_step_mins: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Analyze stored events by sweeping gaps; report the best gap (read-only)."""
        return service.execute(
            "optimize_sessions",
            start_date=start_date,
            end_date=end_date,
            sweep_min_gap_mins=sweep_min_gap_mins,
            sweep_max_gap_mins=sweep_max_gap_mins,
            sweep_step_mins=sweep_step_mins,
        )

    return optimize_sessions


@atomic_tool("unbilled_hours")
def make_unbilled_hours_tool(service: CommandService):
    def unbilled_hours(
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        project_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Report logged-but-not-yet-invoiced timesheet hours (read-only)."""
        return service.execute(
            "unbilled_hours",
            start_date=start_date,
            end_date=end_date,
            project_id=project_id,
        )

    return unbilled_hours


@atomic_tool("unlogged_time_report")
def make_unlogged_time_report_tool(service: CommandService):
    def unlogged_time_report(
        start_date: str,
        end_date: str,
        only_mine: bool = True,
        include_all: bool = False,
    ) -> Dict[str, Any]:
        """Reconcile derived-vs-logged hours per day and task (read-only)."""
        return service.execute(
            "unlogged_time_report",
            start_date=start_date,
            end_date=end_date,
            only_mine=only_mine,
            include_all=include_all,
        )

    return unlogged_time_report


@atomic_tool("normalize_timesheets")
def make_normalize_timesheets_tool(service: CommandService):
    def normalize_timesheets(apply: bool = False) -> Dict[str, Any]:
        """Detect (and optionally merge) duplicate timesheet entries."""
        return service.execute("normalize_timesheets", apply=apply)

    return normalize_timesheets


@atomic_tool("query_sessions")
def make_query_sessions_tool(service: CommandService):
    def query_sessions(
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        task_id: Optional[str] = None,
        repo: Optional[str] = None,
        strategy_name: Optional[str] = None,
        include_events: bool = True,
    ) -> List[Dict[str, Any]]:
        """Query global cross-day sessions overlapping a date range (whole)."""
        return service.execute(
            "query_sessions",
            start_date=start_date,
            end_date=end_date,
            task_id=task_id,
            repo=repo,
            strategy_name=strategy_name,
            include_events=include_events,
        )

    return query_sessions


@atomic_tool("timesheet_summary")
def make_timesheet_summary_tool(service: CommandService):
    def timesheet_summary(
        start_date: str,
        end_date: str,
        group_by: str = "project",
        only_mine: bool = True,
    ) -> Dict[str, Any]:
        """Summarize logged timesheet hours over a date range, grouped one way."""
        return service.execute(
            "timesheet_summary",
            start_date=start_date,
            end_date=end_date,
            group_by=group_by,
            only_mine=only_mine,
        )

    return timesheet_summary


@atomic_tool("search_count")
def make_search_count_tool(service: CommandService):
    def search_count(
        model: str,
        domain: Optional[List[Tuple[str, str, Any]]] = None,
    ) -> int:
        """Count records on a model matching a domain, without fetching them."""
        return service.execute("search_count", model=model, domain=domain)

    return search_count
