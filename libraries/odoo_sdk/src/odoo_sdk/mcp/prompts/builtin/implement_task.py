"""MCP ``implement_task`` prompt surface.

The prompt composes the ``get_task`` command to fetch context and delegates all
message-building to :func:`build_implement_task_messages` in the utilities layer;
no business logic lives inline here.
"""

from odoo_sdk.commands import Registry
from odoo_sdk.utilities.prompt_messages import build_implement_task_messages

from ._registration import builtin_prompt

__all__ = ["make_implement_task_prompt"]

#: ``get_task`` detail sections the rendered prompt needs. Both are opt-in: with
#: no ``include`` the command returns the description only, which would render
#: ``<chatter>(no messages)</chatter>`` no matter what the task really holds.
_INCLUDE = ["description", "chatter"]


@builtin_prompt("implement_task")
def make_implement_task_prompt(command_registry: Registry):
    def implement_task(task_id: int) -> list[str]:
        """Prime the agent to implement an Odoo task using the FSM workflow.

        Fetches full task context (description + chatter) and returns structured
        messages containing the task data and step-by-step workflow instructions.
        Load task_tracker_system_prompt.md as your system prompt before invoking.
        """
        task = command_registry["get_task"].execute(task_id, include=_INCLUDE)
        if task is None:
            raise ValueError(f"Task {task_id} not found.")
        return build_implement_task_messages(task)

    return implement_task
