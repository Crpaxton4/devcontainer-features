"""Built-in command force-stopping every active tracker run (CLI ``stop-all``)."""

from typing import Any

from ..command import Command
from ._registration import builtin_command


@builtin_command
class StopAllRunsCommand(Command):
    """Force-stop every active tracker run in one pass.

    A purely local tracker transition: it snapshots the active
    (``RUNNING``/``AWAITING_ANSWERS``) runs and transitions each to ``STOPPED``.

    Like every stop path it **never** writes ``account.analytic.line`` hours —
    each run only records its local STOPPED transition, and the upload/reconcile
    path bills the elapsed wall-clock later ("upload owns hours"). The runs stay
    billable (no ``aborted_at`` stamp).
    """

    _name = "stop_all"
    _description = (
        "Force-stop every active RUNNING/AWAITING_ANSWERS tracker run, "
        "transitioning each to STOPPED and returning one summary per run stopped. "
        "Never writes timesheet hours — the upload path owns unit_amount; the "
        "runs stay billable (not aborted)."
    )

    def execute(self) -> list[dict[str, Any]]:
        """Stop every active run and return one summary per run stopped.

        The elapsed time is captured from each active run *before* it is stopped,
        so the summary reports the wall-clock accrued while it was running.

        :returns: One ``{"id", "task_name", "elapsed"}`` dict per stopped run, in
            the active-run order (oldest-first); an empty list when nothing was
            active.
        :rtype: list[dict[str, Any]]
        """
        db = self.state
        summaries: list[dict[str, Any]] = []
        for run in db.get_all_active_runs():
            summaries.append(
                {
                    "id": run.id,
                    "task_name": run.task_name,
                    "elapsed": run.elapsed_human,
                }
            )
            db.stop_run(run.task_id)
        return summaries
