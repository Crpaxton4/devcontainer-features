from typing import Any, Optional

from ..command import Command
from ._registration import builtin_command
from odoo_sdk.state import LocalStateClient, TaskNotRunningError, TaskRun, TaskState
from odoo_sdk.utilities.env import assert_odoo_devcontainer
from odoo_sdk.utilities.timesheet import close_anchor


def _find_target_run(db: LocalStateClient, identifier: int) -> Optional[TaskRun]:
    """Resolve a run by its SQLite run id first, then by task id.

    ``abort_run`` accepts either identifier; the run id is tried first so a run
    that has already stopped (which is not "active" and so invisible to a task-id
    lookup) is still found and reported as already-closed.
    """
    run = db.get_run_by_id(identifier)
    if run is not None:
        return run
    return db.get_active_run(identifier)


@builtin_command
class AbortRunCommand(Command):
    """Abort a wedged run in the central tracker DB by run id or task id (#369).

    Before #369 this opened *another* project's per-repo DB by hash. There is now
    one host-provisioned central DB shared across every container, so a wedged run
    started from any checkout is reachable from any container: this force-closes
    it straight to ``STOPPED`` without logging hours and closes out its dangling
    Odoo anchor (only when the anchor is still the unreconciled ``[/] Work in
    progress`` marker, never a human-edited row). It complements ``abort_task``
    (which aborts the *active* run for a task id) by also targeting a specific run
    id and reporting an already-stopped run as a no-op.
    """

    _name = "abort_run"
    _description = (
        "Abort a stale RUNNING/AWAITING_ANSWERS run in the central tracker DB "
        "(addressed by SQLite run id or Odoo task id) and close out its orphaned "
        "Odoo anchor timesheet. Hours are never logged; a human-edited anchor is "
        "never clobbered."
    )

    def execute(self, run_id_or_task_id: int) -> dict[str, Any]:
        """Force-close an orphaned run in the central tracker DB.

        :param run_id_or_task_id: SQLite run id, or the Odoo task id of the run.
        :return: Summary of the aborted run and whether the anchor was closed.
        :raises TaskNotRunningError: When no matching run exists.
        :raises TrackerStateMissingError: When the central DB is not
            host-provisioned at its expected path.
        """
        assert_odoo_devcontainer()
        db = self.state
        run = _find_target_run(db, run_id_or_task_id)
        if run is None:
            raise TaskNotRunningError(
                f"No run {run_id_or_task_id} in the central tracker DB."
            )
        if run.state == TaskState.STOPPED:
            return self._already_stopped_result(run)
        return self._abort_active_run(db, run)

    def _abort_active_run(
        self, target: LocalStateClient, run: TaskRun
    ) -> dict[str, Any]:
        """Stop the active run and retire its Odoo anchor timesheet."""
        stopped = target.abort_run(run.task_id)
        anchor_closed = close_anchor(self._client, stopped.timesheet_id)
        return {
            "run_id": stopped.id,
            "task_id": stopped.task_id,
            "task_name": stopped.task_name,
            "project_name": stopped.project_name,
            "timesheet_id": stopped.timesheet_id,
            "anchor_closed": anchor_closed,
            "aborted": True,
            "already_stopped": False,
        }

    @staticmethod
    def _already_stopped_result(run: TaskRun) -> dict[str, Any]:
        """Report a run that was already STOPPED as a no-op (no Odoo write)."""
        return {
            "run_id": run.id,
            "task_id": run.task_id,
            "task_name": run.task_name,
            "project_name": run.project_name,
            "timesheet_id": run.timesheet_id,
            "anchor_closed": False,
            "aborted": False,
            "already_stopped": True,
        }
