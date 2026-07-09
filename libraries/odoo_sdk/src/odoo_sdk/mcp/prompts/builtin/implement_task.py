"""MCP ``implement_task`` prompt surface.

The prompt composes the ``get_task`` command to fetch context and delegates all
message-building to :func:`build_implement_task_messages` in the utilities layer;
no business logic lives inline here.
"""

from odoo_sdk.commands import Registry
from odoo_sdk.utilities.odoo_helpers import format_chatter as _format_chatter
from odoo_sdk.utilities.prompt_messages import build_implement_task_messages

# Backwards-compatible thin aliases kept so existing callers/tests can import
# these names from this module.
_build_messages = build_implement_task_messages

__all__ = ["make_implement_task_prompt", "_build_messages", "_format_chatter"]


def make_implement_task_prompt(command_registry: Registry):
    def implement_task(task_id: int) -> list[str]:
        """Prime the agent to implement an Odoo task using the FSM workflow.

        Fetches full task context (description + chatter) and returns structured
        messages containing the task data and step-by-step workflow instructions.
        Load task_tracker_system_prompt.md as your system prompt before invoking.
        """
        task = command_registry["get_task"].execute(task_id)
        if task is None:
            raise ValueError(f"Task {task_id} not found.")
        return build_implement_task_messages(task)

    return implement_task
