"""Builtin commands shipped with the SDK.

Each module defines a frozen ``Args`` dataclass and a ``handle(services, args)``
function, and records its :class:`~odoo_dev_core.registry.CommandSpec` with the
:func:`~odoo_dev_core.registry.command` decorator when it imports. The import
list stays explicit on purpose: no ``pkgutil`` scanning, so the set of builtins
stays reviewable. :func:`~odoo_dev_core.registry.default_registry` imports this
package to collect them.
"""

from . import (  # noqa: F401
    assign_event,
    create_task,
    get_activities,
    get_employee_id,
    get_mail_status,
    get_models,
    get_task,
    get_task_attachments,
    get_task_chatter,
    get_tasks,
    get_todo,
    get_uid,
    log_event,
    mark_activity_done,
    normalize_timesheets,
    optimize_sessions,
    query_sessions,
    read_attachment,
    read_knowledge_article,
    resync,
    schedule_activity,
    search_activity_types,
    search_chatter,
    search_count,
    search_knowledge_articles,
    search_projects,
    search_tasks,
    task_aging,
    task_list,
    task_note,
    task_question,
    timesheet_summary,
    unbilled_hours,
    unlogged_time_report,
    upload,
)
