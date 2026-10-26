"""Where the data lives. Each rule copies the producer that writes the data.

The state root follows ``odoo_client/state/dispatch_state.py`` and ``state/db.py``.
The artifacts root follows ``dispatch_state.artifacts_root``. The orchestrator
home follows ``home()`` in ``plugins/odoo-orchestrator/scripts/orchestrator.py``.
This package does not import those modules. Every function reads the
environment on each call, so a test can set it with ``monkeypatch``.
"""

import os
from pathlib import Path


def state_root() -> Path:
    for name in ("ODOO_DISPATCH_STATE_DIR", "ODOO_TASK_TRACKER_DIR"):
        value = os.environ.get(name)
        if value:
            return Path(value)
    xdg = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "state"
    return base / "odoo-task-tracker"


def jobs_db() -> Path:
    return state_root() / "jobs.db"


def runs_db() -> Path:
    return state_root() / "runs.db"


def artifacts_root() -> Path:
    value = os.environ.get("DISPATCH_ARTIFACTS_DIR")
    if value:
        return Path(value).expanduser()
    return Path.home() / ".config" / "odoo-dev" / "dispatch" / "artifacts"


def usage_log_dir() -> Path | None:
    """The directory of usage.db and declarations.db; None when unset."""
    value = os.environ.get("USAGE_LOG_DIR")
    return Path(value).expanduser() if value else None


def orchestrator_home() -> Path:
    override = os.environ.get("ODOO_ORCHESTRATOR_HOME")
    if override:
        return Path(os.path.abspath(os.path.expanduser(override)))
    return Path.home() / ".local" / "state" / "odoo-orchestrator"
