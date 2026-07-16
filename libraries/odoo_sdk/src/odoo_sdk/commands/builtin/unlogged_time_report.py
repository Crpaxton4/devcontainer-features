"""Builtin command exposing the read-only unlogged-time gap report (#378 item 10).

A thin delegator to :func:`odoo_sdk.billing.unlogged_time.unlogged_time_report`,
which composes the existing derivation and billing path with a read-only Odoo
timesheet read — it materializes no session state and writes nothing.
"""

from __future__ import annotations

from typing import Any

from odoo_sdk.billing.unlogged_time import unlogged_time_report

from ..command import Command
from ._registration import builtin_command


@builtin_command
class UnloggedTimeReportCommand(Command):
    """Report per (day, task) derived-vs-logged hours and the delta (read-only)."""

    _name = "unlogged_time_report"
    _description = (
        "Reconcile what an upload WOULD bill against what is already logged in "
        "Odoo, per day and task, over an inclusive date window. Read-only: it "
        "writes nothing and materializes no session state. Derived hours come "
        "from the same session-derivation and billing transform an upload "
        "applies (the min-session floor and rounding), so a single-event session "
        "reports the minimum billable, not zero. Logged hours are summed from "
        "account.analytic.line. Each row is {day, task_id, task, derived_hours, "
        "logged_hours, delta}; only nonzero-delta rows are returned unless "
        "include_all=True. Per-day and window totals cover all cells. Dates are "
        "'YYYY-MM-DD'; hours are the unit. Odoo must be reachable."
    )

    def execute(
        self,
        start_date: str,
        end_date: str,
        only_mine: bool = True,
        include_all: bool = False,
    ) -> dict[str, Any]:
        """Return the unlogged-time gap report for the window.

        :param start_date: Inclusive window start, ``YYYY-MM-DD``.
        :param end_date: Inclusive window end, ``YYYY-MM-DD``.
        :param only_mine: Restrict logged hours to the authenticated user's own
            employee timesheets (matching what an upload would bill).
        :param include_all: Include reconciled (zero-delta) rows too.
        :return: The report dict (see the utility for its shape).
        """
        return unlogged_time_report(
            self._client,
            self.state,
            self.config,
            start_date,
            end_date,
            only_mine=only_mine,
            include_all=include_all,
        )
