"""Cross-project discovery over the task-tracker state root (issue #331).

The task-tracker keys each project's SQLite DB by ``sha256(git remote)[:16]``,
so a DB whose repo is gone — or that holds a wedged ``RUNNING`` run started from
a since-deleted checkout — becomes unreachable from any single working tree. This
module scans the whole state root, reads the repo identity each DB now records
(see :meth:`odoo_sdk.state.db.LocalStateClient._persist_identity`), and surfaces
every project's active runs together with a staleness flag so an operator can
find and abort the orphans.

Nothing here raises on a bad DB: an unreadable or corrupt file is reported as a
skipped note entry rather than aborting the whole scan.
"""

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from .db import LocalStateClient, _resolve_state_root

#: Run states that count as "active" for discovery — a run in any of these is
#: still holding an open Odoo anchor and may be the orphan an operator hunts.
_ACTIVE_STATES = ("RUNNING", "AWAITING_ANSWERS")

#: Label used when a DB predates identity stamping (no ``repo_label`` setting).
UNKNOWN_LABEL = "(unknown)"


def _state_root(root: Optional[Path]) -> Path:
    """Return the explicit ``root`` or the shared env-aware default."""
    return Path(root) if root is not None else _resolve_state_root()


def project_db_path(project_hash: str, root: Optional[Path] = None) -> Path:
    """Return the ``tasks.db`` path for one project hash under the state root."""
    return _state_root(root) / project_hash / "tasks.db"


def _is_stale(started_at: datetime, threshold: datetime) -> bool:
    """Return whether ``started_at`` is older than ``threshold`` (UTC-safe)."""
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    return started_at < threshold


def _run_entry(run: Any, threshold: datetime) -> dict:
    """Render one active run as a discovery dict with a per-run stale flag."""
    return {
        "run_id": run.id,
        "task_id": run.task_id,
        "task_name": run.task_name,
        "state": run.state.value,
        "started_at": run.started_at.isoformat(),
        "timesheet_id": run.timesheet_id,
        "stale": _is_stale(run.started_at, threshold),
    }


def _note_entry(project_hash: str, message: str) -> dict:
    """Return the entry for a DB that could not be read; the scan continues."""
    return {
        "project_hash": project_hash,
        "repo_label": UNKNOWN_LABEL,
        "repo_remote_url": None,
        "active_runs": [],
        "stale": False,
        "note": message,
    }


def _inspect_project(
    db_file: Path, project_hash: str, stale_after_hours: float
) -> dict:
    """Open one project DB and collect its identity and active runs."""
    client = LocalStateClient(db_path=db_file)
    threshold = datetime.now(timezone.utc) - timedelta(hours=stale_after_hours)
    runs = [_run_entry(run, threshold) for run in client.get_all_active_runs()]
    return {
        "project_hash": project_hash,
        "repo_label": client.get_setting("repo_label") or UNKNOWN_LABEL,
        "repo_remote_url": client.get_setting("repo_remote_url"),
        "active_runs": runs,
        "stale": any(run["stale"] for run in runs),
        "note": None,
    }


def discover_projects(
    root: Optional[Path] = None, stale_after_hours: float = 12.0
) -> list[dict]:
    """Scan the state root and describe every task-tracker project it holds.

    Globs ``<state-root>/*/tasks.db`` (``root`` defaulting to the same env-aware
    resolution the DB layer uses) and, for each, records the project hash, the
    ``repo_label``/``repo_remote_url`` identity settings (``"(unknown)"`` /
    ``None`` for DBs predating identity stamping), and every active
    (``RUNNING``/``AWAITING_ANSWERS``) run with its ``run_id``, ``task_id``,
    ``task_name``, ``state``, ``started_at``, ``timesheet_id`` and a per-run
    ``stale`` flag (started before ``stale_after_hours`` ago). A DB that cannot
    be opened or read is reported as a note entry and never aborts the scan.

    :param root: State root to scan; defaults to the env-aware resolution.
    :param stale_after_hours: Age past which an active run is flagged stale.
    :return: One dict per project, sorted by project hash.
    """
    state_root = _state_root(root)
    results: list[dict] = []
    if not state_root.exists():
        return results
    for db_file in sorted(state_root.glob("*/tasks.db")):
        project_hash = db_file.parent.name
        try:
            results.append(
                _inspect_project(db_file, project_hash, stale_after_hours)
            )
        except sqlite3.Error as exc:
            results.append(
                _note_entry(project_hash, f"skipped (unreadable): {exc}")
            )
    return results
