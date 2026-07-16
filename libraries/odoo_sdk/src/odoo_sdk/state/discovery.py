"""Active-run discovery over the single central tracker DB (issues #331, #369).

Before #369 the tracker kept one SQLite DB per git remote, so a run started from
a since-deleted checkout became unreachable from any other working tree and this
module had to glob ``<state-root>/*/tasks.db`` to find it. There is now exactly
one host-provisioned, bind-mounted central DB shared across every project's
container, so discovery collapses to an ordinary query of that one DB: list the
active (``RUNNING``/``AWAITING_ANSWERS``) runs and flag the stale ones so an
operator can abort the orphans with ``abort_run``.

The DB is host-provisioned and never self-created (#369; see
:class:`TrackerStateMissingError`), so an absent DB surfaces that error here too.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from odoo_sdk._utils import as_utc

from .db import LocalStateClient, tracker_db_path


def _is_stale(started_at: datetime, threshold: datetime) -> bool:
    """Return whether ``started_at`` is older than ``threshold`` (UTC-safe)."""
    return as_utc(started_at) < threshold


def _run_entry(run: Any, threshold: datetime) -> dict:
    """Render one active run as a discovery dict with a per-run stale flag."""
    return {
        "run_id": run.id,
        "task_id": run.task_id,
        "task_name": run.task_name,
        "project_name": run.project_name,
        "state": run.state.value,
        "started_at": run.started_at.isoformat(),
        "timesheet_id": run.timesheet_id,
        "stale": _is_stale(run.started_at, threshold),
    }


def discover_runs(
    root: Optional[Path] = None, stale_after_hours: float = 12.0
) -> list[dict]:
    """Return every active run in the central tracker DB, oldest first.

    Opens the one central DB (``root`` defaulting to the env-aware resolution the
    DB layer uses) and reports each active (``RUNNING``/``AWAITING_ANSWERS``) run
    with its ``run_id``, ``task_id``, ``task_name``, ``project_name``, ``state``,
    ``started_at``, ``timesheet_id`` and a per-run ``stale`` flag (started before
    ``stale_after_hours`` ago).

    :raises TrackerStateMissingError: When the central DB has not been
        host-provisioned at its expected path.
    """
    client = LocalStateClient(db_path=tracker_db_path(root))
    threshold = datetime.now(timezone.utc) - timedelta(hours=stale_after_hours)
    return [_run_entry(run, threshold) for run in client.get_all_active_runs()]
