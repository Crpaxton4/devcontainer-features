"""Report per (day, task) derived-vs-logged hours and the delta (read-only)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from odoo_dev_core.billing.unlogged_time import unlogged_time_report

from ..ports import Services
from ..registry import command


@dataclass(frozen=True)
class Args:
    start_date: str
    end_date: str
    only_mine: bool = True
    include_all: bool = False


@command(
    "unlogged_time_report",
    args=Args,
    description="Reconcile what an upload WOULD bill against what is already logged in "
    "Odoo, per day and task, over an inclusive date window. Read-only: it "
    "writes nothing and materializes no session state. Derived hours come "
    "from the same session-derivation and billing transform an upload "
    "applies (the min-session floor and rounding), so a single-event session "
    "reports the minimum billable, not zero. Logged hours are summed from "
    "account.analytic.line. Each row is {day, task_id, task, derived_hours, "
    "logged_hours, delta}; only nonzero-delta rows are returned unless "
    "include_all=True. Per-day and window totals cover all cells. Dates are "
    "'YYYY-MM-DD'; hours are the unit. Odoo must be reachable.",
)
def handle(services: Services, args: Args) -> dict[str, Any]:
    """Return the unlogged-time gap report for the window.

    :param start_date: Inclusive window start, ``YYYY-MM-DD``.
    :param end_date: Inclusive window end, ``YYYY-MM-DD``.
    :param only_mine: Restrict logged hours to the authenticated user's own
        employee timesheets (matching what an upload would bill).
    :param include_all: Include reconciled (zero-delta) rows too.
    :return: The report dict (see the utility for its shape).
    """
    return unlogged_time_report(
        services.client,
        services.events,
        services.config,
        args.start_date,
        args.end_date,
        only_mine=args.only_mine,
        include_all=args.include_all,
    )
