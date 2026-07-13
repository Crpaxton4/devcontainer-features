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
        """Fetch task context for a project task with opt-in extra detail.

        Base identity fields (name, project, stage, assignees, deadline,
        priority, tags) are always returned. ``include`` selects extra, more
        expensive detail; each entry is one of: ``description``, ``chatter``,
        ``dependencies``, ``timesheets``, ``subtasks``. When omitted the cheap
        default is description only.
        """
        return registry["get_task"].execute(task_id, include=include)

    return get_task


@atomic_tool("get_task_chatter")
def make_get_task_chatter_tool(registry: Registry):
    def get_task_chatter(task_id: int, limit: int = 100) -> List[dict]:
        """Fetch all chatter messages for a task, sorted oldest-first."""
        return registry["get_task_chatter"].execute(task_id, limit=limit)

    return get_task_chatter


@atomic_tool("get_task_attachments")
def make_get_task_attachments_tool(registry: Registry):
    def get_task_attachments(
        task_id: int, include_content: bool = False
    ) -> List[dict]:
        """List a task's attachments from both the task and its chatter.

        Each entry always carries metadata: ``id``, ``name``, ``mimetype``,
        ``file_size``, ``create_date``, and ``source`` (``task`` or ``message``),
        deduped by attachment id. Raw bytes are opt-in: with the default
        ``include_content=False`` the base64 ``datas`` payload is omitted to keep
        the call cheap; set ``True`` to include it.
        """
        return registry["get_task_attachments"].execute(
            task_id, include_content=include_content
        )

    return get_task_attachments


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
        """Full-text search across Odoo chatter (``mail.message``) bodies.

        Matches ``query`` case-insensitively against message bodies
        (``body ilike``) and returns the newest matches first, capped at
        ``limit``. Read-only.

        Optional filters (all combinable):

        * ``model`` — restrict to messages on one Odoo model, e.g.
          ``"project.task"``.
        * ``record_id`` — restrict to one record's conversation; pair with
          ``model`` to target a specific record.
        * ``date_from`` / ``date_to`` — inclusive message-timestamp bounds as
          ``YYYY-MM-DD`` strings (``date_to`` compares against the start of that
          day).

        Each result carries ``id``, ``date``, ``author``, ``type``, ``subtype``,
        an HTML-stripped Markdown ``body``, and the originating ``res_model`` /
        ``res_id`` so the source record can be located.
        """
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
        """Search the Odoo Knowledge base (``knowledge.article``) by text.

        Matches ``query`` case-insensitively against each article's ``name``
        **or** its ``body`` (an OR ``ilike`` domain) and returns the most
        recently updated articles first (``write_date desc``, then ``id desc``),
        capped at ``limit``. Read-only: no article is created or modified.

        Each result carries ``id``, ``name``, a ``snippet`` (the article body
        converted from HTML to Markdown and capped at 500 characters), and
        ``write_date`` (``YYYY-MM-DD HH:MM:SS``).

        ``knowledge.article`` is an Odoo **Enterprise** model. On a Community
        database (or when the Knowledge app is not installed) this raises an
        error — "knowledge.article model not available (Odoo Enterprise
        required)" — rather than returning results.
        """
        return registry["search_knowledge_articles"].execute(query, limit=limit)

    return search_knowledge_articles


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
        """Force-close a wedged session without logging hours; drop its timesheet."""
        return registry["abort_task"].execute(task_id)

    return abort_task


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


@atomic_tool("ingest_sessions")
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


@atomic_tool("unbilled_hours")
def make_unbilled_hours_tool(registry: Registry):
    def unbilled_hours(
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        project_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Report logged-but-not-yet-invoiced timesheet hours (read-only).

        Returns a summary envelope ``{mode, count, total_hours, lines}``.
        ``total_hours`` and each line's ``hours`` are decimal hours; each line
        carries ``id``, ``date``, ``employee``, ``project``, ``task``, ``hours``
        and ``name``.

        Semantics depend on a ``fields_get`` capability probe of
        ``account.analytic.line`` and are reported in ``mode``:

        * ``"full"`` — both ``timesheet_invoice_id`` and
          ``timesheet_invoice_type`` exist: unbilled means not posted to any
          customer invoice, and every line adds ``invoice_type`` (billable vs
          non-billable).
        * ``"fallback"`` — only one field exists: unbilled is approximated as
          "not linked to a sale order line"; ``invoice_type`` is omitted.
        * neither field exists: returns a structured error payload.

        ``start_date``/``end_date`` are inclusive ``YYYY-MM-DD`` bounds (omit for
        unbounded); ``project_id`` is a ``project.project`` id restricting the
        report to one project.
        """
        return registry["unbilled_hours"].execute(
            start_date=start_date, end_date=end_date, project_id=project_id
        )

    return unbilled_hours


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
        """Summarize logged timesheet hours over a date range, grouped one way.

        Dates are ``YYYY-MM-DD`` and inclusive on both ends. Hours are the unit —
        Odoo's ``unit_amount`` on ``account.analytic.line`` — summed per group.
        ``group_by`` selects the single axis to collapse onto:

        * ``project`` — total hours per task's project.
        * ``client`` — total hours per project's partner (the customer); a
          project with no partner is grouped under a ``null`` label.
        * ``task`` — total hours per individual task.
        * ``day`` — total hours per calendar day, with ``YYYY-MM-DD`` labels.

        ``only_mine=True`` (default) restricts the summary to the authenticated
        user's own employee timesheets; ``False`` includes every timesheet the
        user can see. An invalid ``group_by`` or a malformed date raises
        ``ValueError``.

        Returns a dict with ``group_by``, the echoed ``start_date``/``end_date``,
        ``only_mine``, ``unit`` (always ``"hours"``), a ``groups`` list of
        ``{label, hours, entries}`` objects, and a grand ``total_hours``.
        """
        return registry["timesheet_summary"].execute(
            start_date,
            end_date,
            group_by=group_by,
            only_mine=only_mine,
        )

    return timesheet_summary

