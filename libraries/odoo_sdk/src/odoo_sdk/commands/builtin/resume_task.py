from datetime import datetime, timezone
from typing import Any

from ..command import Command
from ._registration import builtin_command
from odoo_sdk.utilities.env import assert_odoo_devcontainer


@builtin_command
class ResumeTaskCommand(Command):
    """Resume a paused task session back to RUNNING.

    A thin alias of the ``start_task`` ensure semantics (#621) for callers that
    already hold a session: two predecessors resume (#504) — an
    ``AWAITING_ANSWERS`` session (after stakeholder answers arrive) and a
    ``STOPPED`` session (work continues after a stop) — the stopped run is
    reopened in place, preserving its original start so one effort stays one
    run. An already-RUNNING session is a no-op success (#621), so retries are
    safe. The transition is the whole command: no chatter note is posted (#505).
    The former fixed ``"Resuming implementation with received answers."`` marker
    carried no information the event row does not already record, and its
    unguarded post could raise after the state had already moved to RUNNING.
    """

    _name = "resume_task"
    _description = (
        "Resume a paused task session back to RUNNING. Session state machine: "
        "AWAITING_ANSWERS -> RUNNING (after stakeholder answers arrive); "
        "STOPPED (non-aborted) -> RUNNING, reopened in place rather than "
        "started anew; already RUNNING -> no-op success (idempotent). Errors "
        "only when the task has no resumable session (aborted/CLOSED/none) — "
        "use start_task, the idempotent lifecycle entry point, to create one."
    )

    def execute(self, task_id: int) -> dict[str, Any]:
        """Ensure the task's session is RUNNING (resume, or no-op if running).

        :param task_id: Odoo project.task record id.
        :return: Confirmation with task name and resumed_at timestamp.
        """
        assert_odoo_devcontainer()
        db = self.state
        run = db.transition_to_running(task_id)
        resumed_at = datetime.now(timezone.utc).isoformat()
        return {
            "task_name": run.task_name,
            "project_name": run.project_name,
            "state": run.state.value,
            "resumed_at": resumed_at,
        }
