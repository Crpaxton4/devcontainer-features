from typing import Any, Optional

from ..command import Command
from ._registration import builtin_command
from odoo_sdk.state import LocalStateClient, TaskNotRunningError, TaskRun, TaskState
from odoo_sdk.state.discovery import project_db_path
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
    """Abort a wedged run in *another* project's DB, keyed by project hash (#331).

    The cross-DB analog of ``abort_task``: it opens the target project's SQLite
    DB by hash under the shared state root regardless of the current working
    directory, force-closes the orphaned run without logging hours, and closes
    out its dangling Odoo anchor (only when the anchor is still the unreconciled
    ``[/] Work in progress`` marker, never a human-edited row).
    """

    _name = "abort_run"
    _description = (
        "Abort a stale RUNNING/AWAITING_ANSWERS run in another project's tracker "
        "DB (addressed by project hash) and close out its orphaned Odoo anchor "
        "timesheet. Hours are never logged; a human-edited anchor is never "
        "clobbered."
    )

    def execute(
        self, project_hash: str, run_id_or_task_id: int
    ) -> dict[str, Any]:
        """Force-close an orphaned run in the project DB named by ``project_hash``.

        :param project_hash: Directory name of the target project's DB under the
            state root (the ``sha256(remote)[:16]`` hash).
        :param run_id_or_task_id: SQLite run id, or the Odoo task id of the run.
        :return: Summary of the aborted run and whether the anchor was closed.
        :raises ValueError: When no tracker DB exists for ``project_hash``.
        :raises TaskNotRunningError: When no matching run exists in that DB.
        """
        assert_odoo_devcontainer()
        db_path = project_db_path(project_hash)
        if not db_path.exists():
            raise ValueError(
                f"No task-tracker database for project hash {project_hash!r}."
            )
        target = LocalStateClient(db_path=db_path)
        run = _find_target_run(target, run_id_or_task_id)
        if run is None:
            raise TaskNotRunningError(
                f"No run {run_id_or_task_id} in project {project_hash!r}."
            )
        if run.state == TaskState.STOPPED:
            return self._already_stopped_result(project_hash, run)
        return self._abort_active_run(target, project_hash, run)

    def _abort_active_run(
        self, target: LocalStateClient, project_hash: str, run: TaskRun
    ) -> dict[str, Any]:
        """Stop the active run and retire its Odoo anchor timesheet."""
        stopped = target.stop_run(run.task_id)
        anchor_closed = close_anchor(self._client, stopped.timesheet_id)
        return {
            "project_hash": project_hash,
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
    def _already_stopped_result(
        project_hash: str, run: TaskRun
    ) -> dict[str, Any]:
        """Report a run that was already STOPPED as a no-op (no Odoo write)."""
        return {
            "project_hash": project_hash,
            "run_id": run.id,
            "task_id": run.task_id,
            "task_name": run.task_name,
            "project_name": run.project_name,
            "timesheet_id": run.timesheet_id,
            "anchor_closed": False,
            "aborted": False,
            "already_stopped": True,
        }
