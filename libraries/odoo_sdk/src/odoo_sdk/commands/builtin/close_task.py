from typing import Any

from ..command import Command
from ._registration import builtin_command
from odoo_sdk.state import TaskNotRunningError


@builtin_command
class CloseTaskCommand(Command):
    """Move a task's open run to the terminal ``CLOSED`` state (#504).

    The FIRST deliberately CLI-only builtin. ``CLOSED`` distinguishes a finished
    effort from a merely-paused one: unlike ``STOPPED`` it is never reopened by
    ``resume_task`` or by ``start_task``'s auto-resume, and it is hidden from the
    default run queries. It is registered as a builtin (so the CLI can dispatch it
    through the shared registry) but has **no** ``mcp/tools`` factory, so it never
    reaches the MCP wire surface — the agent cannot see or reason about ``CLOSED``
    (issue #504's explicit non-goal). A purely local tracker transition: it writes
    no ``account.analytic.line`` hours and makes no Odoo call, so it needs no
    devcontainer/Odoo connection.

    It closes the task's live run (``RUNNING``/``AWAITING_ANSWERS``) or, when none
    is live, its most recent resumable ``STOPPED`` run, and reports a run that has
    nothing open to close as ``closed=False`` rather than raising.
    """

    _name = "close_task"
    _description = (
        "Close a task's tracking run into the terminal CLOSED state — a finished "
        "run that resume/auto-resume never reopens and that the default run "
        "listings hide. Local-only; writes no timesheet hours."
    )

    def execute(self, task_id: int) -> dict[str, Any]:
        """Close the open run for ``task_id`` and describe the outcome.

        :param task_id: Odoo project.task record id whose run to close.
        :returns: ``{"closed", "task_id", "run_id", "task_name", "state"}``.
            ``closed`` is ``False`` (with ``run_id``/``task_name``/``state``
            ``None``) when the task had no live or resumable run to close.
        :rtype: dict[str, Any]
        """
        db = self.state
        try:
            run = db.close_run(task_id)
        except TaskNotRunningError:
            return {
                "closed": False,
                "task_id": task_id,
                "run_id": None,
                "task_name": None,
                "state": None,
            }
        return {
            "closed": True,
            "task_id": task_id,
            "run_id": run.id,
            "task_name": run.task_name,
            "state": run.state.value,
        }
