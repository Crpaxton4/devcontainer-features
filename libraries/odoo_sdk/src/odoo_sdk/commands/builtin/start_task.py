from typing import Any, Optional

from ..command import Command
from ._registration import builtin_command
from odoo_sdk.state import TaskAlreadyRunningError, TaskState
from odoo_sdk.utilities.checkpoint import checkpoint_hint
from odoo_sdk.utilities.env import assert_odoo_devcontainer


def _build_run_result(
    run: Any,
    task_id: int,
    task_name: str,
    project_name: str,
    *,
    already_running: bool = False,
    branch_name: Optional[str] = None,
    warning: Optional[str] = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "run_id": run.id,
        "task_id": task_id,
        "task_name": task_name,
        "project_name": project_name,
        "state": run.state.value,
        "started_at": run.started_at.isoformat(),
        "timesheet_id": run.timesheet_id,
        "already_running": already_running,
    }
    if branch_name is not None:
        result["branch_name"] = branch_name
    if warning is not None:
        result["warning"] = warning
    return result


@builtin_command
class StartTaskCommand(Command):
    """Idempotently ensure a RUNNING tracking session for a resolved task (#621).

    This command is atomic and surface-agnostic: it takes already-resolved task and
    project identity (the MCP tool performs any name-search disambiguation and git
    branch setup) and ensures a RUNNING local tracking session, dispatching on the
    task's current session state:

    * ``RUNNING`` — no-op success: the existing run is returned with
      ``already_running: true`` and zero side effects.
    * ``AWAITING_ANSWERS`` — transitioned back to ``RUNNING``.
    * ``STOPPED`` (non-aborted) — resumed in place (#504): the same run row is
      reopened with its original ``started_at``, so one continuous effort stays
      one run.
    * aborted / ``CLOSED`` / no run — a fresh run is created.

    As of #325 the FSM writes **no** ``account.analytic.line`` row: the former
    0-hour ``[/] Work in progress`` anchor is gone. Billable hours are derived
    end-to-end from captured events by the sessionization → ETL upload path, which
    is the sole owner of every timesheet write, so ``run.timesheet_id`` stays
    ``None`` until the upload path materializes the derived session.

    No chatter note is posted (#505). The former fixed ``"Work started on this
    task."`` marker carried no information the event row does not already
    record; the run and its events are the tracking record.
    """

    _name = "start_task"
    _description = (
        "Idempotently ensure a RUNNING tracking session for a resolved Odoo "
        "project.task — safe to call from automation in any session state. "
        "Session state machine: no session / aborted / CLOSED -> creates a new "
        "RUNNING run; STOPPED -> resumes the stopped run in place; "
        "AWAITING_ANSWERS -> transitions back to RUNNING; already RUNNING -> "
        "no-op success returning the existing run with already_running=true. "
        "Takes resolved task and project identifiers (no name-search or "
        "confirmation prompts); writes no Odoo timesheet and posts no chatter "
        "note (the sessionization/ETL upload path owns all timesheet hours)."
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
        """Ensure a RUNNING tracking session for an already-resolved task.

        Idempotent (#621): dispatches on the current session state — RUNNING is
        a no-op success (``already_running: true``), AWAITING_ANSWERS and a
        resumable STOPPED run are transitioned back to RUNNING, and only an
        absent/aborted/CLOSED session creates a fresh run. It never raises for
        an existing session.

        No Odoo timesheet is written (#325) and no chatter note is posted
        (#505): the run is recorded in local state only. Hours are derived by
        the sessionization/ETL upload path, the sole timesheet writer.

        :param task_id: Resolved Odoo project.task id.
        :param task_name: Resolved task display name.
        :param project_id: Resolved Odoo project id.
        :param project_name: Resolved project display name.
        :param branch_name: Optional git branch created for the task, echoed back.
        :param warning: Optional non-fatal warning to include in the result.
        :return: Session details including task name, project, started_at, state,
            and ``already_running`` (``timesheet_id`` is ``None`` — no anchor is
            created).
        """
        assert_odoo_devcontainer()

        db = self.state
        already_running = False
        existing = db.get_active_run(task_id)
        if existing is not None and existing.state is TaskState.RUNNING:
            # No-op (#621): the session is already RUNNING; return it unchanged.
            run = existing
            already_running = True
        elif existing is not None or db.get_resumable_run(task_id) is not None:
            # AWAITING_ANSWERS or resumable STOPPED (#504): reopen the run in
            # place so one continuous effort keeps a single run row and its
            # original start, instead of inserting a second row for the same work.
            run = db.transition_to_running(task_id)
        else:
            # No timesheet anchor is created: the FSM performs no ``account.
            # analytic.line`` write (#325). ``timesheet_id`` stays NULL until the
            # upload path materializes the derived session.
            try:
                run = db.create_run(
                    task_id=task_id,
                    task_name=task_name,
                    project_id=project_id,
                    project_name=project_name,
                )
            except TaskAlreadyRunningError:
                # Lost create race (#621): another writer opened a session
                # between the check and the insert. Idempotency still holds —
                # ensure the surviving session is RUNNING and report it as
                # pre-existing rather than erroring the retry-safe entry point.
                run = db.transition_to_running(task_id)
                already_running = True
        result = _build_run_result(
            run, task_id, task_name, project_name,
            already_running=already_running,
            branch_name=branch_name,
            warning=warning,
        )
        # Prime the checkpoint-cadence signal (#387) on the very first response:
        # a fresh run has no note yet, so this reports ~0 minutes and no nudge.
        result.update(checkpoint_hint(db, task_id, run.started_at))
        return result
