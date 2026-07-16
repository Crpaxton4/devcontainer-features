"""Billing subsystem: the tracker's ``account.analytic.line`` write layer.

This package gathers the tracker's most consequential logic — everything that
turns tracked work into billed Odoo timesheet rows — into one place:

* :mod:`~odoo_sdk.billing.timesheet` — the sole owner of ``account.analytic.line``
  writes (anchor lifecycle, session reconciliation, orphan sweep, merges).
* :mod:`~odoo_sdk.billing.upload` — the shared upload orchestration plus the
  billable-hours transform (:func:`~odoo_sdk.billing.upload._billable_hours` /
  :func:`~odoo_sdk.billing.upload._round_to_step`).
* :mod:`~odoo_sdk.billing.unlogged_time` — derived-vs-logged reconciliation.
* :mod:`~odoo_sdk.billing.timesheet_reports` — read-side ``read_group``
  aggregation (formerly ``utilities/timesheets.py``; renamed to end the
  ``timesheet.py`` vs ``timesheets.py`` near-name trap).
"""

from .timesheet import (
    ABORTED_ANCHOR_NAME,
    ANCHOR_NAME,
    ORPHANED_UPLOAD_NAME,
    close_anchor,
    ensure_anchor,
    merge_timesheets,
    reconcile_session,
    resolve_employee_id,
    sweep_orphaned_uploads,
    update_timesheet,
)
from .timesheet_reports import timesheet_summary
from .upload import range_bounds, upload_sessions
from .unlogged_time import unlogged_time_report

__all__ = [
    "ANCHOR_NAME",
    "ABORTED_ANCHOR_NAME",
    "ORPHANED_UPLOAD_NAME",
    "close_anchor",
    "ensure_anchor",
    "merge_timesheets",
    "reconcile_session",
    "resolve_employee_id",
    "sweep_orphaned_uploads",
    "update_timesheet",
    "timesheet_summary",
    "unlogged_time_report",
    "range_bounds",
    "upload_sessions",
]
