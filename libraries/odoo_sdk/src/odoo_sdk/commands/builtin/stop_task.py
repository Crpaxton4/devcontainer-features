from typing import Any

from ..command import Command, require_active_run
from ._registration import builtin_command
from odoo_sdk.state.summary import summarize_run_activity
from odoo_sdk.utilities.env import assert_odoo_devcontainer


@builtin_command
class StopTaskCommand(Command):
    """Stop a task tracking session and finalize the local session state.

    Atomic and surface-agnostic: it takes only the task id and never references
    MCP. The run's narrative is never asked of a human (#623) — it is a
    machine-derived summary computed from the run's recorded events and notes
    (#626) and stored on the run row (``task_runs.run_summary``), where the
    billing upload picks it up as the timesheet entry's description. The
    summary is internal/local text with NO length cap; the 300-character
    chatter limit applies only to posted chatter bodies.

    This command does **not** write hours to the Odoo timesheet, and
    ``start_task`` creates no timesheet anchor to close out; the elapsed hours
    are written to Odoo later by the TUI/ETL upload path (which owns all
    ``account.analytic.line`` hour writes). Stop only transitions the run to
    STOPPED and records the local session data.
    """

    _name = "stop_task"
    _description = (
        "Stop an active task tracking session. Transitions the run to stopped "
        "and stores a machine-derived run summary computed from the run's "
        "recorded events and notes. Does not write hours to Odoo — the TUI/ETL "
        "upload path owns timesheet hours."
    )

    def execute(self, task_id: int) -> dict[str, Any]:
        """Stop the active session for a task and record it locally.

        Does not write hours to Odoo; ``elapsed_hours`` is computed and returned
        for callers/tests to display, but the timesheet hour write is owned by
        the TUI/ETL upload path. The run summary is derived automatically —
        there is no description parameter and no elicitation (#623).

        :param task_id: Odoo project.task record id.
        :return: Summary with task name, elapsed time, and the derived
            ``run_summary`` (``None`` when the run recorded no events or notes).
        """
        assert_odoo_devcontainer()
        db = self.state
        run = require_active_run(db, task_id)
        elapsed_hours = run.elapsed_hours

        # Events are read over the run's own window (started_at .. now) BEFORE
        # the stop lands, so the summary reflects exactly the work this run saw.
        events = db.get_task_events(str(task_id), start=run.started_at)
        run_summary = summarize_run_activity(events, run.notes) or None

        stopped = db.stop_run(task_id)
        if run_summary is not None:
            db.set_run_summary(stopped.id, run_summary)

        return {
            "run_id": stopped.id,
            "task_name": stopped.task_name,
            "project_name": stopped.project_name,
            "elapsed": stopped.elapsed_human,
            "elapsed_hours": round(elapsed_hours, 4),
            "run_summary": run_summary,
            "timesheet_id": stopped.timesheet_id,
        }
