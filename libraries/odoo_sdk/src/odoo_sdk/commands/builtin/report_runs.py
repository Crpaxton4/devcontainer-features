"""Built-in command reporting tracker runs (CLI ``report``)."""

from typing import Any

from ..command import Command
from ._registration import builtin_command
from odoo_sdk.utilities.runs import run_summary


@builtin_command
class ReportRunsCommand(Command):
    """Report tracker runs, optionally including the stopped ones.

    A read-only, purely local query of the tracker DB — it needs no Odoo
    connection. It backs the CLI ``report`` subcommand: the default lists only
    the active runs, while ``include_stopped`` widens the report to every run on
    record.
    """

    _name = "report_runs"
    _description = (
        "Report tracker runs with elapsed time. By default only the active "
        "RUNNING/AWAITING_ANSWERS runs are returned; pass include_stopped=True "
        "to include STOPPED runs too. Read-only; needs no Odoo connection."
    )

    def execute(self, include_stopped: bool = False) -> list[dict[str, Any]]:
        """Return one summary dict per reported run.

        :param include_stopped: When ``True``, include STOPPED runs; otherwise
            report only the active runs.
        :type include_stopped: bool
        :returns: Run summaries (see
            :func:`~odoo_sdk.utilities.runs.run_summary`), ordered oldest-first.
        :rtype: list[dict[str, Any]]
        """
        runs = (
            self.state.get_all_runs()
            if include_stopped
            else self.state.get_all_active_runs()
        )
        return [run_summary(run) for run in runs]
