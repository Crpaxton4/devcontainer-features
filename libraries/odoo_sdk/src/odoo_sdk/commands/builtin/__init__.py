"""Built-in commands shipped with the SDK.

These commands form the default tool surface exposed when the MCP server is run
directly (``odoo-mcp`` / ``python -m odoo_sdk.mcp``). Consumers who define their
own commands register them on a :class:`Registry` and start the server
themselves; see ``examples/general/mcp_custom_commands_example.py``.

``BUILTIN_COMMANDS`` is populated by the :func:`builtin_command` decorator (see
:mod:`._registration`) as each command module below is imported. The import list
stays explicit on purpose — no ``pkgutil`` scanning — so the built-in surface
stays reviewable and stable for mutation testing. Importing a module both runs
its decorator (registering the command) and binds the class as a package
attribute.
"""

from ..command_registry import Registry
from ._registration import BUILTIN_COMMANDS, builtin_command
from .abort_run import AbortRunCommand
from .abort_task import AbortTaskCommand
from .assign_event import AssignEventCommand
from .create_task import CreateTaskCommand
from .discover_runs import DiscoverRunsCommand
from .get_employee_id import GetEmployeeIdCommand
from .get_mail_status import GetMailStatusCommand
from .get_models import GetModelsCommand
from .get_task import GetTaskCommand
from .get_task_attachments import GetTaskAttachmentsCommand
from .get_task_chatter import GetTaskChatterCommand
from .get_tasks import GetTasksCommand
from .get_todo import GetTodoCommand
from .get_uid import GetUidCommand
from .list_runs import ListRunsCommand
from .normalize_timesheets import NormalizeTimesheetsCommand
from .optimize_sessions import OptimizeSessionsCommand
from .query_sessions import QuerySessionsCommand
from .read_attachment import ReadAttachmentCommand
from .read_knowledge_article import ReadKnowledgeArticleCommand
from .report_runs import ReportRunsCommand
from .resume_task import ResumeTaskCommand
from .resync import ResyncCommand
from .search_chatter import SearchChatterCommand
from .search_count import SearchCountCommand
from .search_knowledge_articles import SearchKnowledgeArticlesCommand
from .search_projects import SearchProjectsCommand
from .search_tasks import SearchTasksCommand
from .start_task import StartTaskCommand
from .stop_all_runs import StopAllRunsCommand
from .stop_run import StopRunCommand
from .stop_task import StopTaskCommand
from .task_aging import TaskAgingCommand
from .task_list import TaskListCommand
from .task_note import TaskNoteCommand
from .task_question import TaskQuestionCommand
from .task_status import TaskStatusCommand
from .timesheet_summary import TimesheetSummaryCommand
from .unbilled_hours import UnbilledHoursCommand
from .unlogged_time_report import UnloggedTimeReportCommand


def register_builtins(registry: Registry) -> Registry:
    """Register every built-in command on ``registry`` and return it (chaining)."""
    for command_name, command in BUILTIN_COMMANDS.items():
        registry.register(command_name, command)
    return registry


# ``__all__`` is derived from the decorator-populated registry so the command
# class exports stay in lockstep with ``BUILTIN_COMMANDS`` automatically: a
# decorated-and-imported command needs no third hand-maintained list. Each
# ``__name__`` equals the class binding imported above (Python's naming
# convention), so every derived entry is a real package attribute; the leading
# names cover the registration API itself.
__all__ = [
    "BUILTIN_COMMANDS",
    "builtin_command",
    "register_builtins",
    *sorted(command.__name__ for command in BUILTIN_COMMANDS.values()),
]
