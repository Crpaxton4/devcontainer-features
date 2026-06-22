"""Built-in commands shipped with the SDK.

These commands form the default tool surface exposed when the MCP server is run
directly (``odoo-mcp`` / ``python -m odoo_sdk.mcp``). Consumers who define their
own commands register them on a :class:`Registry` and start the server
themselves; see ``examples/general/mcp_custom_commands_example.py``.
"""

from ..command_registry import Registry
from .create_task import CreateTaskCommand
from .get_models import GetModelsCommand
from .get_tasks import GetTasksCommand
from .get_todo import GetTodoCommand
from .get_uid import GetUidCommand
from .resume_task import ResumeTaskCommand
from .start_task import StartTaskCommand
from .stop_task import StopTaskCommand
from .task_list import TaskListCommand
from .task_note import TaskNoteCommand
from .task_question import TaskQuestionCommand
from .task_status import TaskStatusCommand

BUILTIN_COMMANDS = {
    "get_uid": GetUidCommand,
    "get_models": GetModelsCommand,
    "get_tasks": GetTasksCommand,
    "get_todo": GetTodoCommand,
    "create_task": CreateTaskCommand,
    "start_task": StartTaskCommand,
    "stop_task": StopTaskCommand,
    "resume_task": ResumeTaskCommand,
    "task_status": TaskStatusCommand,
    "task_note": TaskNoteCommand,
    "task_list": TaskListCommand,
    "task_question": TaskQuestionCommand,
}


def register_builtins(registry: Registry) -> Registry:
    """Register every built-in command on ``registry``.

    :param registry: Registry to populate with the built-in commands.
    :type registry: Registry
    :return: The same registry, returned for convenient chaining.
    :rtype: Registry
    """

    for command_name, command in BUILTIN_COMMANDS.items():
        registry.register(command_name, command)
    return registry


__all__ = [
    "BUILTIN_COMMANDS",
    "register_builtins",
    "GetUidCommand",
    "GetModelsCommand",
    "GetTasksCommand",
    "GetTodoCommand",
    "CreateTaskCommand",
    "StartTaskCommand",
    "StopTaskCommand",
    "ResumeTaskCommand",
    "TaskStatusCommand",
    "TaskNoteCommand",
    "TaskListCommand",
    "TaskQuestionCommand",
]
