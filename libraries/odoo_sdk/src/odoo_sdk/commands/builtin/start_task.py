import logging
from datetime import date
from typing import Any, Optional

from ..command import Command
from ._registration import builtin_command
from odoo_sdk.state import TaskAlreadyRunningError
from odoo_sdk.utilities.checkpoint import checkpoint_hint
from odoo_sdk.utilities.env import assert_odoo_devcontainer
from odoo_sdk.utilities.odoo_helpers import post_chatter_note
from odoo_sdk.billing.timesheet import ensure_anchor, resolve_employee_id

log = logging.getLogger(__name__)


def _build_run_result(
    run: Any,
    task_id: int,
    task_name: str,
    project_name: str,
    timesheet_id: int,
    *,
    branch_name: Optional[str] = None,
    warning: Optional[str] = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "run_id": run.id,
        "task_id": task_id,
        "task_name": task_name,
        "project_name": project_name,
        "started_at": run.started_at.isoformat(),
        "timesheet_id": timesheet_id,
    }
    if branch_name is not None:
        result["branch_name"] = branch_name
    if warning is not None:
        result["warning"] = warning
    return result


@builtin_command
class StartTaskCommand(Command):
    """Start time-tracking a resolved project task, creating a placeholder timesheet.

    This command is atomic and surface-agnostic: it takes already-resolved task and
    project identity (the MCP tool performs any name-search disambiguation, user
    confirmation, and git branch setup) and creates the timesheet entry, chatter
    note, and local session.

    The timesheet entry is a lightweight 0-hour ``[/] Work in progress`` anchor
    for task-board visibility only. It is later closed out (real hours and final
    description written) by the TUI/ETL upload path, not by ``stop_task``.
    """

    _name = "start_task"
    _description = (
        "Begin tracking time on a resolved Odoo project.task. Takes resolved task "
        "and project identifiers (no name-search or confirmation prompts). Creates a "
        "placeholder timesheet entry in Odoo and a local tracking session."
    )

    def execute(
        self,
        task_id: int,
        task_name: str,
        project_id: int,
        project_name: str,
        branch_name: Optional[str] = None,
        warning: Optional[str] = None,
    ) -> dict[str, Any]:
        """Start a tracking session for an already-resolved task.

        :param task_id: Resolved Odoo project.task id.
        :param task_name: Resolved task display name.
        :param project_id: Resolved Odoo project id.
        :param project_name: Resolved project display name.
        :param branch_name: Optional git branch created for the task, echoed back.
        :param warning: Optional non-fatal warning to include in the result.
        :return: Session details including task name, project, and started_at.
        :raises TaskAlreadyRunningError: When the task already has an active
            session.
        """
        assert_odoo_devcontainer()

        db = self.state
        existing = db.get_active_run(task_id)
        if existing is not None:
            raise TaskAlreadyRunningError(
                f"Task {task_name!r} already has an active session "
                f"(id={existing.id}, state={existing.state.value})."
            )

        employee_id = resolve_employee_id(self._client, db)

        timesheet_id = ensure_anchor(
            self._client, task_id, project_id, employee_id, date.today()
        )
        # If the local run insert fails, re-raise so the failure surfaces
        # loudly. The freshly-created anchor is intentionally left in Odoo:
        # record deletion (unlink) is purposefully not implemented for safety.
        run = db.create_run(
            task_id=task_id,
            task_name=task_name,
            project_id=project_id,
            project_name=project_name,
            timesheet_id=timesheet_id,
        )
        # The chatter note is a best-effort announcement, not tracking state:
        # the run is already RUNNING above. If the post fails (network/Odoo
        # hiccup), swallowing it keeps start_task idempotent — a bare failure
        # would return an error while leaving the run RUNNING, so a retry would
        # hit TaskAlreadyRunningError and wedge the caller (issue #361). This
        # mirrors how telemetry emission is swallowed in mcp/server.py.
        try:
            post_chatter_note(self._client, task_id, "Work started on this task.")
        except Exception:
            log.warning(
                "start_task chatter note failed for task %s; run is RUNNING "
                "and start succeeded regardless.",
                task_id,
                exc_info=True,
            )

        result = _build_run_result(
            run, task_id, task_name, project_name, timesheet_id,
            branch_name=branch_name,
            warning=warning,
        )
        # Prime the checkpoint-cadence signal (#387) on the very first response:
        # a fresh run has no note yet, so this reports ~0 minutes and no nudge.
        result.update(checkpoint_hint(db, task_id, run.started_at))
        return result
