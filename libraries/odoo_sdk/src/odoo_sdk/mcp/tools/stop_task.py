"""MCP ``stop_task`` tool: stops the task's active tracking session.

Fully automatic (#623): the former ``description`` parameter and its
review/edit elicitation were a no-op human gate — the value was never persisted
and had zero downstream consumers — so both are gone. The run's narrative is
the machine-derived summary ``stop_task`` computes from the run's recorded
events and notes (#626); nothing is asked of a human on stop.
"""

from typing import Any

from odoo_sdk.commands import Registry

from .composition import composition_tool


@composition_tool("stop_task")
def make_stop_task_tool(registry: Registry):
    """Build the async ``stop_task`` MCP tool bound to ``registry``.

    :param registry: Command registry providing the stop command.
    :type registry: Registry
    :return: Async callable implementing the ``stop_task`` tool.
    """

    async def stop_task(task_id: int) -> dict[str, Any]:
        """Stop an active task tracking session.

        Stops with no prompt at all: hours are owned by the TUI/ETL upload
        path, and the run summary is derived automatically from the run's
        recorded events and notes (#626) — never elicited (#623).
        """
        # Raise-based error contract (#223): a command failure (e.g. no active
        # session -> ``TaskNotRunningError``) is deliberately left to propagate.
        # This flow does no cleanup, so the typed exception is handed straight to
        # the MCP ``_error_boundary`` (#222) rather than being caught and
        # re-wrapped into an ``{"error": ...}`` dict here.
        return registry["stop_task"].execute(task_id)

    return stop_task
