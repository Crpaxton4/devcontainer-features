"""Stale-run reaper: bulk-abort wedged runs and stop attaching events to them (#366).

A dead devcontainer leaves its ``RUNNING`` / ``AWAITING_ANSWERS`` run stuck in the
central tracker DB forever, and — worse — ``log-event --attach-active-run`` keeps
attaching fresh hook events to that stuck run, accruing phantom billable
wall-clock. Recovery used to be manual (``discover`` only reports; ``abort`` is
one run at a time).

**Premise shift since #366 was filed.** There is now exactly ONE host-provisioned
central ``tracker.db`` (no per-project DBs, no cross-DB discovery), and abort
semantics landed in #385: :meth:`LocalStateClient.abort_run` stamps
``task_runs.aborted_at`` and the local abort best-effort closes the Odoo anchor
(renaming an *unedited* ``[/] Work in progress`` row to ``[/] aborted stale run``
at 0h). Sessions inside an aborted run's window are excluded from upload. This
module reaps every stale run through that SAME path, so a reaped run is excluded
from billing exactly like a manually-aborted one.

**"Last activity" definition.** A run's last activity is the most recent of (a)
the latest event attributed to its task id and (b) its own ``started_at`` (the
fallback when the task has no events yet). A run is stale when its last activity
predates the threshold. This is deliberately activity-aware rather than the
cheaper ``started_at``-only flag :func:`discover_runs` renders as an operator
hint: a run still receiving genuine hook events is never reaped just for being
old, and once a run crosses the threshold the attachment exclusion below freezes
its activity so it stays reapable (idempotent — a second reap of the same state
is a no-op because the first left every stale run ``STOPPED``).

**Interaction with resumable STOPPED runs (#504).** The reaper only ever touches
ACTIVE (``RUNNING``/``AWAITING_ANSWERS``) runs, so it never disturbs a run a user
deliberately stopped and may later resume. And because :meth:`abort_run` stamps
``aborted_at``, a reaped run is a *voided* ``STOPPED`` run, which
:meth:`LocalStateClient.get_resumable_run` excludes — so ``start_task`` opens a
fresh run for a reaped task rather than silently resuming the abandoned one.
"""

import math
import os
from datetime import datetime, timedelta, timezone

from odoo_sdk._utils import as_utc
from odoo_sdk.client import OdooClient
from odoo_sdk.state import LocalStateClient, TaskNotRunningError, TaskRun
from odoo_sdk.billing.timesheet import close_anchor

#: Default staleness horizon (hours) for both ``reap`` and the attachment
#: exclusion when no ``--older-than`` flag or env override is given.
DEFAULT_REAP_THRESHOLD_HOURS = 12.0

#: Env override the ``--attach-active-run`` exclusion reads for its threshold, so
#: the mitigation can be tuned without a flag. ``reap`` takes its threshold from
#: ``--older-than`` (default :data:`DEFAULT_REAP_THRESHOLD_HOURS`).
REAP_THRESHOLD_ENV = "ODOO_REAP_THRESHOLD_HOURS"


def threshold_from_hours(hours: float) -> datetime:
    """Return the cutoff instant: runs last active before it are stale."""
    return datetime.now(timezone.utc) - timedelta(hours=hours)


def resolve_env_threshold_hours() -> float:
    """Return the exclusion threshold in hours from the env, else the default.

    Reads :data:`REAP_THRESHOLD_ENV`; a missing, blank, or unparseable value
    falls back to :data:`DEFAULT_REAP_THRESHOLD_HOURS` so a typo never silently
    disables the mitigation. A non-positive or non-finite (``inf``/``nan``)
    override is ignored for the same reason — a zero/negative threshold would
    mark every run stale, and ``inf`` would overflow :func:`threshold_from_hours`
    and crash this hot hook path.
    """
    raw = os.environ.get(REAP_THRESHOLD_ENV)
    if not raw:
        return DEFAULT_REAP_THRESHOLD_HOURS
    try:
        hours = float(raw)
    except ValueError:
        return DEFAULT_REAP_THRESHOLD_HOURS
    if not math.isfinite(hours) or hours <= 0:
        return DEFAULT_REAP_THRESHOLD_HOURS
    return hours


def run_last_activity(db: LocalStateClient, run: TaskRun) -> datetime:
    """Return a run's last-activity instant (see this module's docstring)."""
    started = as_utc(run.started_at)
    latest = db.latest_event_timestamp_for_task(run.task_id)
    if latest is None:
        return started
    return max(as_utc(latest), started)


def is_run_stale(db: LocalStateClient, run: TaskRun, threshold: datetime) -> bool:
    """Return whether a run's last activity predates ``threshold``."""
    return run_last_activity(db, run) < threshold


def stale_active_runs(db: LocalStateClient, threshold: datetime) -> list[TaskRun]:
    """Return the active runs whose last activity predates ``threshold``.

    Ordered oldest-first (``get_all_active_runs`` orders by ``started_at``), so a
    dry-run listing and the reap itself present the same stable order.
    """
    return [run for run in db.get_all_active_runs() if is_run_stale(db, run, threshold)]


def reap_run(db: LocalStateClient, client: OdooClient, run: TaskRun) -> bool:
    """Abort one stale run via the local abort path; best-effort close its anchor.

    Mirrors ``abort_task`` (#385): the local ``abort_run`` stamps ``aborted_at``
    unconditionally (excluding the run from billing), then the Odoo anchor close
    is wrapped best-effort so an unreachable Odoo never leaves the run un-reaped —
    reap still works offline, and the unclosed anchor stays a harmless 0-hour row.

    Resilient to a concurrent stop/abort of the same run (another container
    reaped or stopped it between selection and now): the local abort raises
    :class:`TaskNotRunningError` when the run is no longer active, which is
    treated as an already-handled no-op so a bulk sweep never aborts mid-loop.

    :return: ``True`` when the anchor was closed, ``False`` otherwise (no anchor,
        an already-edited anchor, an Odoo hiccup, or a concurrently-stopped run).
    """
    try:
        stopped = db.abort_run(run.task_id)
    except TaskNotRunningError:
        return False
    try:
        return close_anchor(client, stopped.timesheet_id)
    except Exception:
        return False
