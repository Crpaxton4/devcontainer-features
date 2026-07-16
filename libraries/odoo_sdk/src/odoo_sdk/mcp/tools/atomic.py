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


@atomic_tool("get_mail_status")
def make_get_mail_status_tool(registry: Registry):
    def get_mail_status(res_model: str, res_id: int) -> List[dict]:
        """Report outgoing-mail (``mail.mail``) delivery status for a record.

        Read-only. Joins the record's chatter messages to their linked outbound
        mails and returns, per mail: ``mail_id``, ``message_id``, ``subject``, a
        ``recipients`` summary, the delivery ``state`` (``outgoing`` / ``sent`` /
        ``exception`` / ``cancel``), the message ``date``, and — only when
        populated — ``failure_reason`` / ``failure_type``. Use it to verify
        "send an email" acceptance criteria: pass ``res_model="project.task"``
        with the task id to check a task's outbound mail. Records with only
        chatter notes return an empty list. Never retries or requeues mail.
        ``mail.mail`` is often admin-restricted; a denied read returns a clear
        access error.
        """
        return registry["get_mail_status"].execute(res_model, res_id)

    return get_mail_status


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


@atomic_tool("read_attachment")
def make_read_attachment_tool(registry: Registry):
    def read_attachment(attachment_id: int, mode: str = "text") -> Dict[str, Any]:
        """Read one document already stored in Odoo (read-only).

        Reads an existing ``ir.attachment``; it never uploads or attaches
        anything. ``mode`` selects what is returned:

        * ``metadata`` — identity only, no bytes: ``id``, ``name``, ``mimetype``,
          ``file_size``, ``res_model``, ``res_id``, ``create_date``.
        * ``text`` — decode the binary payload and convert it to Markdown via
          markitdown (PDF / docx / xlsx / CSV / HTML → Markdown). The decoded
          payload is capped at 10 MiB; a larger payload is truncated before
          conversion and the result carries ``truncated: true``. An unsupported
          or unconvertible format (or an empty payload) degrades to ``text=""``
          plus an explanatory ``note`` — never a raised error.
        * ``raw`` — the base64 ``datas`` payload, refusing anything over the
          10 MiB cap with a ``ValueError`` naming the size and the cap.

        A missing or inaccessible ``attachment_id`` raises the missing-record
        error; an invalid ``mode`` raises ``ValueError``.
        """
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


@atomic_tool("read_knowledge_article")
def make_read_knowledge_article_tool(registry: Registry):
    def read_knowledge_article(article_id: int) -> Dict[str, Any]:
        """Read one Odoo Knowledge article (``knowledge.article``) by id.

        Returns the article's **full** body converted from HTML to Markdown
        (not the capped search snippet). Read-only: nothing is created or
        modified.

        The result carries ``id``, ``name``, ``body`` (the full Markdown, capped
        at 50000 characters), ``write_date`` (``YYYY-MM-DD HH:MM:SS``), and a
        ``truncated`` boolean that is ``True`` only when the body exceeded that
        cap and was shortened.

        An unknown ``article_id`` raises "knowledge.article <id> not found".
        ``knowledge.article`` is an Odoo **Enterprise** model: on a Community
        database (or when the Knowledge app is not installed) this raises
        "knowledge.article model not available (Odoo Enterprise required)"
        rather than returning content.
        """
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
        """Attribute tracker events to an Odoo task id in one transaction.

        The triage write: sets ``task_ids`` on every listed event (an
        unattributed meeting/email or a whole calendar series) so they become
        derivable and billable. Validates a positive integer ``task_id`` and
        returns the number of event rows updated.
        """
        return registry["assign_event"].execute(event_ids=event_ids, task_id=task_id)

    return assign_event


@atomic_tool("discover_runs")
def make_discover_runs_tool(registry: Registry):
    def discover_runs(stale_after_hours: float = 12.0) -> List[Dict[str, Any]]:
        """Discover active runs in the central tracker DB.

        Read-only local query: lists the active RUNNING/AWAITING_ANSWERS runs in
        the one host-provisioned central DB, flagging any started before
        ``stale_after_hours`` ago as stale so orphaned runs can be found.
        """
        return registry["discover_runs"].execute(stale_after_hours=stale_after_hours)

    return discover_runs


@atomic_tool("abort_run")
def make_abort_run_tool(registry: Registry):
    def abort_run(run_id_or_task_id: int) -> Dict[str, Any]:
        """Abort a stale run in the central tracker DB and close its Odoo anchor.

        Addresses the run by SQLite run id or Odoo task id in the one central DB
        (regardless of cwd), force-closes it without logging hours, and retires
        its orphaned anchor timesheet (only when still unreconciled).
        """
        return registry["abort_run"].execute(run_id_or_task_id)

    return abort_run


@atomic_tool("resync")
def make_resync_tool(registry: Registry):
    def resync(sources: str = "git,github,odoo") -> Dict[str, Any]:
        """Reconcile local event state against git, GitHub, and Odoo chatter.

        Manual, current-repo-scoped, idempotent reconciliation: pulls authored
        git commits, merged GitHub PRs and reviews, and the authenticated user's
        Odoo task chatter into the local events table, deduped by external id so a
        re-run inserts nothing. Any source whose tool is absent or unauthenticated
        is skipped (never fatal). ``sources`` is a comma-separated subset of
        ``git,github,odoo`` (default: all three). Returns a per-source summary
        dict of ``{"inserted": n}`` / ``{"skipped": reason}``.
        """
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


@atomic_tool("unlogged_time_report")
def make_unlogged_time_report_tool(registry: Registry):
    def unlogged_time_report(
        start_date: str,
        end_date: str,
        only_mine: bool = True,
        include_all: bool = False,
    ) -> Dict[str, Any]:
        """Reconcile derived-vs-logged hours per day and task (read-only).

        Answers the question the manual reconciliation answered: over an
        inclusive ``YYYY-MM-DD`` window, how do the hours an upload *would* bill
        (derived from the event stream, through the same session derivation and
        billing transform an upload applies — the min-session floor and rounding,
        the aborted-run exclusion, the non-numeric-task skip) compare against the
        hours *already logged* in Odoo (``account.analytic.line``)?

        Read-only: it writes nothing, uploads nothing, and materializes no
        session state — the derived side is a local dry-run bill, and only the
        logged-hours read touches Odoo.

        Each derived session is bucketed onto its start day (the day an upload
        would bill it). The result carries the echoed window, ``only_mine`` /
        ``include_all`` flags, ``unit`` (always ``"hours"``), a ``days`` list —
        each ``{day, rows, derived_hours, logged_hours, delta}`` where every row
        is ``{day, task_id, task, derived_hours, logged_hours, delta}`` — and the
        window ``total_derived_hours`` / ``total_logged_hours`` /
        ``total_delta_hours``.

        By default only rows with a nonzero delta are returned (the unlogged or
        over-logged gaps); ``include_all=True`` keeps the reconciled zero-delta
        rows too. Per-day and window totals always cover every cell.
        ``only_mine=True`` (default) restricts logged hours to the authenticated
        user's own employee timesheets. An empty window returns an empty report;
        an unreachable Odoo raises a single clear error.
        """
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
        """List the active tracker runs with elapsed time.

        Read-only local query of the tracker DB: returns the active
        RUNNING/AWAITING_ANSWERS runs, each with its run id, task id, task and
        project names, state, and human-readable elapsed time.
        """
        return registry["list_runs"].execute()

    return list_runs


@atomic_tool("report_runs")
def make_report_runs_tool(registry: Registry):
    def report_runs(include_stopped: bool = False) -> List[Dict[str, Any]]:
        """Report tracker runs, optionally including the stopped ones.

        Read-only local query of the tracker DB. By default only the active
        runs are returned; set ``include_stopped=True`` to include STOPPED runs
        too. Each row carries the run id, task id, task and project names, state,
        and human-readable elapsed time.
        """
        return registry["report_runs"].execute(include_stopped=include_stopped)

    return report_runs


@atomic_tool("stop_run")
def make_stop_run_tool(registry: Registry):
    def stop_run(run_id: int) -> Dict[str, Any]:
        """Force-stop one tracker run by its SQLite run id.

        Transitions the run to STOPPED without logging hours (the upload path
        owns unit_amount; the run stays billable). Reports a missing or
        already-stopped run instead of raising.
        """
        return registry["stop_run"].execute(run_id)

    return stop_run


@atomic_tool("stop_all")
def make_stop_all_tool(registry: Registry):
    def stop_all() -> List[Dict[str, Any]]:
        """Force-stop every active tracker run.

        Transitions each active RUNNING/AWAITING_ANSWERS run to STOPPED without
        logging hours (the upload path owns unit_amount; the runs stay billable)
        and returns one summary per run stopped.
        """
        return registry["stop_all"].execute()

    return stop_all


@atomic_tool("normalize_timesheets")
def make_normalize_timesheets_tool(registry: Registry):
    def normalize_timesheets(apply: bool = False) -> Dict[str, Any]:
        """Detect (and optionally merge) duplicate timesheet entries.

        Finds stopped runs of the same task on the same calendar date that each
        carry their own timesheet. With ``apply=False`` (default) it only reports
        the duplicate groups; with ``apply=True`` it merges each group into its
        lowest timesheet id and remaps the local runs.
        """
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

