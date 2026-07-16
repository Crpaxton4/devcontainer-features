"""MCP ``stop_task`` tool: elicits a reviewed description, then stops the task.

The description review/edit elicitation is an MCP concern and lives here; the
atomic :class:`StopTaskCommand` receives the confirmed description as a plain
primitive.
"""

from typing import Any

from fastmcp import Context
from pydantic import BaseModel

from odoo_sdk.commands import Registry

from .composition import composition_tool


class _ReviewDescription(BaseModel):
    description: str


@composition_tool("stop_task")
def make_stop_task_tool(registry: Registry):
    """Build the async ``stop_task`` MCP tool bound to ``registry``.

    :param registry: Command registry providing the stop command.
    :type registry: Registry
    :return: Async callable implementing the ``stop_task`` tool.
    """

    async def stop_task(
        task_id: int,
        description: str,
        ctx: Context,
    ) -> dict[str, Any]:
        """Stop an active task tracking session.

        Presents the AI-generated work description for review before logging.
        Updates the Odoo timesheet with elapsed hours and the confirmed
        description prefixed with [/].
        """
        review = await ctx.elicit(
            "Review the timesheet description before logging:",
            _ReviewDescription,
        )
        if review.action != "accept":
            return {"error": "Stop task cancelled."}

        confirmed_description = review.data.description or description
        # Raise-based error contract (#223): a command failure (e.g. no active
        # session -> ``TaskNotRunningError``) is deliberately left to propagate.
        # This flow does no cleanup, so the typed exception is handed straight to
        # the MCP ``_error_boundary`` (#222) rather than being caught and
        # re-wrapped into an ``{"error": ...}`` dict here.
        return registry["stop_task"].execute(task_id, confirmed_description)

    return stop_task
