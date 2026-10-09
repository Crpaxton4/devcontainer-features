"""MCP ``implement_task`` prompt surface.

The prompt composes the ``get_task`` command to fetch context and delegates all
message-building to :func:`~odoo_sdk.mcp.prompts.messages.build_implement_task_messages`
(the prompt-content module beside this package, #717); no business logic lives
inline here.
"""

import os
from typing import Optional

from odoo_sdk.commands import Registry
from odoo_sdk.mcp.prompts.messages import build_implement_task_messages

from ._registration import builtin_prompt

__all__ = ["make_implement_task_prompt"]

#: ``get_task`` detail sections the rendered prompt needs. Both are opt-in: with
#: no ``include`` the command returns the description only, which would render
#: ``<chatter>(no messages)</chatter>`` no matter what the task really holds.
_INCLUDE = ["description", "chatter"]

#: The odoo-dev plugin's state-dir variable. ``scripts/state-dir.sh`` is the
#: single definition of how it resolves, and it forbids any caller from
#: hardcoding its ``$HOME/.local/share/odoo-dev`` default a second time — so
#: this module reads the variable and stays silent when it is unset rather than
#: guessing a path the plugin might disagree with (#992).
_STATE_DIR_ENV = "ODOO_DEV_STATE_DIR"


def _resolve_artifacts_dir(task_id: int) -> Optional[str]:
    """Resolve the task's artifacts directory from the server environment.

    :param task_id: Odoo task id naming the per-task directory.
    :type task_id: int
    :return: ``<state-dir>/tasks/<task-id>``, or ``None`` when
        ``ODOO_DEV_STATE_DIR`` is unset.
    :rtype: Optional[str]
    """
    state_dir = os.environ.get(_STATE_DIR_ENV)
    if not state_dir:
        return None
    return f"{state_dir.rstrip('/')}/tasks/{task_id}"


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
        return build_implement_task_messages(
            task, artifacts_dir=_resolve_artifacts_dir(task_id)
        )

    return implement_task
