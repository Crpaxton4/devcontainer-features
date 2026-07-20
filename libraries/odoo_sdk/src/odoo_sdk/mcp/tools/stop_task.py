"""MCP ``stop_task`` tool: optionally reviews a description, then stops the task.

The description is optional (#482): time logging moved to the odoo-tui/ETL
upload path, so stopping no longer needs a timesheet-style summary. When a
caller does supply one, the review/edit elicitation is an MCP concern and lives
here; the atomic :class:`StopTaskCommand` receives the confirmed description as
a plain primitive.
"""

from typing import Any, Optional

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
        ctx: Context,
        description: Optional[str] = None,
    ) -> dict[str, Any]:
        """Stop an active task tracking session.

        The work description is optional and purely informational — hours are
        owned by the TUI/ETL upload path, so nothing is written to the Odoo
        timesheet here. When a description is supplied it is presented for
        review before the session is stopped; when it is omitted the session
        stops with no prompt at all.
        """
        confirmed_description = description
        if description is not None:
            review = await ctx.elicit(
                "Review the work description before stopping:",
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
