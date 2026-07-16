"""Built-in command force-stopping one tracker run by id (CLI ``stop``)."""

from typing import Any

from ..command import Command
from ._registration import builtin_command
from odoo_sdk.state import TaskState


@builtin_command
class StopRunCommand(Command):
    """Force-stop one tracker run addressed by its SQLite run id.

    A purely local tracker transition: it looks the run up by run id, and — when
    it is still active — transitions it to ``STOPPED``. It complements
    :class:`~odoo_sdk.commands.builtin.stop_task.StopTaskCommand` (which stops the
    active run for an Odoo *task* id) by addressing a specific *run* id, the shape
    the CLI ``list``/``report`` tables expose, and by reporting a missing or
    already-stopped run rather than raising.

    This command **never** writes ``account.analytic.line`` hours. Like every
    stop path it only records the local STOPPED transition; the elapsed
    wall-clock is billed later by the upload/reconcile path, which owns all
    ``unit_amount`` writes ("upload owns hours"). Unlike
    :class:`~odoo_sdk.commands.builtin.abort_run.AbortRunCommand` it leaves the
    run billable (no ``aborted_at`` stamp) and never touches the Odoo anchor.
    """

    _name = "stop_run"
    _description = (
        "Force-stop one tracker run addressed by its SQLite run id, "
        "transitioning it to STOPPED. Reports a missing or already-stopped run "
        "instead of raising. Never writes timesheet hours — the upload path owns "
        "unit_amount; the run stays billable (not aborted)."
    )

    def execute(self, run_id: int) -> dict[str, Any]:
        """Stop the run with ``run_id`` and describe the outcome.

        :param run_id: SQLite run id from the ``list``/``report`` tables.
        :type run_id: int
        :returns: ``{"found", "already_stopped", "run_id", "task_name",
            "elapsed"}``. ``found`` is ``False`` for an unknown run;
            ``already_stopped`` is ``True`` when the run was already STOPPED
            (a no-op).
        :rtype: dict[str, Any]
        """
        db = self.state
        run = db.get_run_by_id(run_id)
        if run is None:
            return {
                "found": False,
                "already_stopped": False,
                "run_id": run_id,
                "task_name": None,
                "elapsed": None,
            }
        if run.state == TaskState.STOPPED:
            return {
                "found": True,
                "already_stopped": True,
                "run_id": run.id,
                "task_name": run.task_name,
                "elapsed": run.elapsed_human,
            }
        stopped = db.stop_run(run.task_id)
        return {
            "found": True,
            "already_stopped": False,
            "run_id": stopped.id,
            "task_name": stopped.task_name,
            "elapsed": stopped.elapsed_human,
        }
