from typing import Any

from ..command import Command, require_active_run
from ._registration import builtin_command
from odoo_sdk.utilities.env import assert_odoo_devcontainer
from odoo_sdk.billing.timesheet import close_anchor


@builtin_command
class AbortTaskCommand(Command):
    """Force-close a wedged task session without writing any hours (#356).

    Unlike ``stop_task``, this never logs elapsed time. It moves a stuck
    ``RUNNING`` / ``AWAITING_ANSWERS`` session straight to ``STOPPED``, stamps the
    run ``aborted_at`` so the upload path excludes its leftover sessions, and
    retires the run's orphaned ``[/] Work in progress`` anchor in Odoo by
    renaming it to ``[/] aborted stale run`` at 0 hours — exactly the anchor
    handling the cross-DB ``abort_run`` performs. The row is never deleted (the
    SDK never deletes records) and a human-edited anchor is never clobbered.
    The anchor close is best-effort: unwedging must never fail on Odoo
    connectivity, and billing is prevented by the ``aborted_at`` stamp alone.
    """

    _name = "abort_task"
    _description = (
        "Force-close a wedged RUNNING or AWAITING_ANSWERS task session without "
        "logging hours, retiring its orphaned Odoo anchor so it never bills."
    )

    def execute(self, task_id: int) -> dict[str, Any]:
        """Force-close the active session for a task without logging hours.

        :param task_id: Odoo project.task record id.
        :return: Summary of the aborted session, including whether the anchor was
            closed.
        :raises TaskNotRunningError: When there is no active session to abort.
        """
        assert_odoo_devcontainer()
        db = self.state
        require_active_run(db, task_id)

        stopped = db.abort_run(task_id)
        try:
            anchor_closed = close_anchor(self._client, stopped.timesheet_id)
        except Exception:
            # Best-effort, like start_task's chatter post (#375): unwedging a
            # stuck session is a local escape hatch and must never fail on Odoo
            # connectivity. Billing is already prevented by the ``aborted_at``
            # exclusion, and the unclosed anchor stays a harmless 0-hour row.
            anchor_closed = False

        return {
            "run_id": stopped.id,
            "task_name": stopped.task_name,
            "project_name": stopped.project_name,
            "elapsed": stopped.elapsed_human,
            "aborted": True,
            "timesheet_id": stopped.timesheet_id,
            "anchor_closed": anchor_closed,
        }
