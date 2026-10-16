"""State root of the task dispatcher (design section 11).

The root is a directory on a local filesystem. It holds::

    <root>/jobs.db              job store (disposable working state)
    <root>/runs.db              run record (irreplaceable evidence)
    <root>/dispatcher.lock      singleton advisory lock file (never a database)
    $DISPATCH_ARTIFACTS_DIR/<job_id>/  bundle per job; not under <root> (odoo-dev bind)

Resolution order: ``$ODOO_DISPATCH_STATE_DIR`` (override), then
``$ODOO_TASK_TRACKER_DIR`` (the tracker's own root), then the tracker default
(:func:`odoo_sdk.state.db._default_root`). The two new stores are separate
files from ``tracker.db`` and create themselves on first use.
"""

import os
import time
from pathlib import Path

from .db import _default_root

#: Override for the dispatcher root. Wins over :data:`TRACKER_DIR_ENV`.
DISPATCH_STATE_DIR_ENV = "ODOO_DISPATCH_STATE_DIR"
#: The tracker's state root variable; the dispatcher default.
TRACKER_DIR_ENV = "ODOO_TASK_TRACKER_DIR"
#: The bundle root on the host. It must sit under :func:`odoo_dev_dir`, which
#: the personal-features Feature binds into every container.
DISPATCH_ARTIFACTS_DIR_ENV = "DISPATCH_ARTIFACTS_DIR"

JOBS_DB_FILENAME = "jobs.db"
RUNS_DB_FILENAME = "runs.db"
LOCK_FILENAME = "dispatcher.lock"
ARTIFACTS_DIRNAME = "artifacts"


def now_us() -> int:
    """Return the current time as integer microseconds since the epoch, UTC."""
    return time.time_ns() // 1000


def resolve_now_us(explicit: int | None) -> int:
    """Return ``explicit`` when given (even 0), else the current clock."""
    return now_us() if explicit is None else explicit


def state_root() -> Path:
    """Resolve the dispatcher state root (see the module docstring)."""
    for name in (DISPATCH_STATE_DIR_ENV, TRACKER_DIR_ENV):
        value = os.environ.get(name)
        if value:
            return Path(value)
    return _default_root()


def jobs_db_path(root: Path | None = None) -> Path:
    """Return the path of ``jobs.db`` under ``root`` (default: the state root)."""
    return (root or state_root()) / JOBS_DB_FILENAME


def runs_db_path(root: Path | None = None) -> Path:
    """Return the path of ``runs.db`` under ``root`` (default: the state root)."""
    return (root or state_root()) / RUNS_DB_FILENAME


def lock_path(root: Path | None = None) -> Path:
    """Return the path of the dispatcher lock file."""
    return (root or state_root()) / LOCK_FILENAME


def odoo_dev_dir() -> Path:
    """Return the host directory the Feature binds at ``/usr/local/share/odoo-dev``."""
    return Path.home() / ".config" / "odoo-dev"


def artifacts_root(root: Path | None = None) -> Path:
    """Return the bundle root, which the Feature's odoo-dev bind carries.

    An explicit ``root`` (tests) gives ``<root>/artifacts``. Otherwise
    ``$DISPATCH_ARTIFACTS_DIR``, then ``~/.config/odoo-dev/dispatch/artifacts``.
    The state root plays no part, so ``jobs.db`` and ``runs.db`` stay local.
    """
    if root is not None:
        return root / ARTIFACTS_DIRNAME
    value = os.environ.get(DISPATCH_ARTIFACTS_DIR_ENV)
    if value:
        return Path(value).expanduser()
    return odoo_dev_dir() / "dispatch" / ARTIFACTS_DIRNAME


def bundle_dir(job_id: str, root: Path | None = None) -> Path:
    """Return ``artifacts/<job_id>/``; ``job_id`` must be one path component."""
    if (
        not isinstance(job_id, str)
        or not job_id
        or job_id in (".", "..")
        or "/" in job_id
        or "\\" in job_id
    ):
        raise ValueError(f"job_id is not a single path component: {job_id!r}")
    return artifacts_root(root) / job_id
