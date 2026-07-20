"""Built-in command listing the active tracker runs (CLI ``list``)."""

from typing import Any

from ..command import Command
from ._registration import builtin_command
from odoo_sdk.utilities.runs import run_summary


@builtin_command
class ListRunsCommand(Command):
    """List the active (``RUNNING``/``AWAITING_ANSWERS``) tracker runs.

    A read-only, purely local query of the tracker DB — it needs no Odoo
    connection. It backs the CLI ``list`` subcommand and is reusable by any
    frontend that wants the current active-run snapshot without the extra
    staleness metadata :class:`~odoo_sdk.commands.builtin.discover_runs.
    DiscoverRunsCommand` computes.
    """

    _name = "list_runs"
    _description = (
        "List the active RUNNING/AWAITING_ANSWERS tracker runs with elapsed "
        "time. Read-only; needs no Odoo connection."
    )

    def execute(self) -> list[dict[str, Any]]:
        """Return one summary dict per active run.

        :returns: Active-run summaries (see
            :func:`~odoo_sdk.utilities.runs.run_summary`), ordered oldest-first.
        :rtype: list[dict[str, Any]]
        """
        return [run_summary(run) for run in self.state.get_all_active_runs()]
