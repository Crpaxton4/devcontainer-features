from typing import Any

from ..command import Command
from ._registration import builtin_command
from odoo_sdk.state.discovery import discover_runs


@builtin_command
class DiscoverRunsCommand(Command):
    """List every active run in the central tracker DB (#331, #369).

    A read-only, purely local query of the one host-provisioned central DB: it
    needs no Odoo connection. It surfaces the active
    (``RUNNING``/``AWAITING_ANSWERS``) runs with a staleness flag, so an operator
    can find wedged runs — including ones started from a since-deleted checkout,
    now visible because every project shares the one DB — and abort them with
    ``abort_run``.
    """

    _name = "discover_runs"
    _description = (
        "Discover active RUNNING/AWAITING_ANSWERS runs in the central tracker "
        "DB, each flagged stale when started before the staleness threshold. "
        "Read-only; needs no Odoo connection."
    )

    def execute(self, stale_after_hours: float = 12.0) -> list[dict[str, Any]]:
        """Return one dict per active run (see :func:`discover_runs`).

        :param stale_after_hours: Age past which an active run is flagged stale.
        :return: The discovery report, one entry per active run.
        """
        return discover_runs(stale_after_hours=stale_after_hours)
