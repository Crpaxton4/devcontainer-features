from typing import Any

from ..command import Command
from ._registration import builtin_command
from odoo_sdk.state.discovery import discover_projects


@builtin_command
class DiscoverRunsCommand(Command):
    """List every task-tracker project and its active runs across all DBs (#331).

    A read-only, purely local scan of the tracker state root: it needs no Odoo
    connection. It surfaces each project's recorded repo identity and its active
    (``RUNNING``/``AWAITING_ANSWERS``) runs with a staleness flag, so an operator
    can find runs orphaned in DBs keyed by an opaque repo hash and abort them
    with ``abort_run``.
    """

    _name = "discover_runs"
    _description = (
        "Discover every task-tracker project across all local DBs, listing each "
        "project's repo identity and active RUNNING/AWAITING_ANSWERS runs, each "
        "flagged stale when started before the staleness threshold. Read-only; "
        "needs no Odoo connection."
    )

    def execute(self, stale_after_hours: float = 12.0) -> list[dict[str, Any]]:
        """Return one dict per discovered project (see :func:`discover_projects`).

        :param stale_after_hours: Age past which an active run is flagged stale.
        :return: The discovery report, one entry per project.
        """
        return discover_projects(stale_after_hours=stale_after_hours)
