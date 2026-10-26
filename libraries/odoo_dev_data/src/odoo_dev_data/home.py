"""The ONE resolver of the odoo-dev state root.

On the host the root is ``$ODOO_DEV_HOME`` or, when that is unset or empty,
``~/.config/odoo-dev``. Every container bind-mounts the root at
``CONTAINER_HOME``. That constant is shared with the dispatcher and the
feature. Resolution does not consult it: inside a container the environment
sets ``ODOO_DEV_HOME`` to it.

This module is the only place that names the layout under the root and the
only place that creates directories.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

ENV_HOME = "ODOO_DEV_HOME"
DEFAULT_HOME = "~/.config/odoo-dev"
CONTAINER_HOME = "/usr/local/share/odoo-dev"

_TASK_ID = re.compile(r"^[0-9]+$")
_JOB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True)
class Paths:
    """Every well-known path under one state root."""

    home: Path
    config_toml: Path
    repo_map: Path
    state_db: Path
    usage_db: Path
    tasks: Path
    locks: Path


def resolve_home(env: Mapping[str, str] | None = None) -> Path:
    """Return the state root. No filesystem access."""
    env = os.environ if env is None else env
    return Path(env.get(ENV_HOME) or DEFAULT_HOME).expanduser()


def paths(home: Path | None = None) -> Paths:
    """Return the layout under ``home`` (default: the resolved root)."""
    root = resolve_home() if home is None else home
    return Paths(
        home=root,
        config_toml=root / "config.toml",
        repo_map=root / "repo-map.json",
        state_db=root / "state.db",
        usage_db=root / "usage.db",
        tasks=root / "tasks",
        locks=root / "locks",
    )


def ensure_home(home: Path | None = None, mode: int = 0o700) -> Paths:
    """Create the root, ``tasks/`` and ``locks/``. The one mkdir site.

    ``mode`` is applied to the root only when this call created it.
    """
    layout = paths(home)
    created = not layout.home.exists()
    layout.home.mkdir(parents=True, exist_ok=True)
    if created:
        layout.home.chmod(mode)
    layout.tasks.mkdir(exist_ok=True)
    layout.locks.mkdir(exist_ok=True)
    return layout


def task_dir(task_id: int | str, home: Path | None = None) -> Path:
    """Return ``tasks/<id>``. Raise ``ValueError`` for a non-numeric id."""
    if not _TASK_ID.fullmatch(str(task_id)):
        raise ValueError(f"invalid task id: {task_id!r}")
    return paths(home).tasks / str(task_id)


def ensure_task_dir(task_id: int | str, home: Path | None = None) -> Path:
    """Create ``tasks/<id>`` (and the root) and return it."""
    ensure_home(home)
    path = task_dir(task_id, home)
    path.mkdir(exist_ok=True)
    return path


def job_bundle_dir(task_id: int | str, job_id: str, home: Path | None = None) -> Path:
    """Return ``tasks/<id>/jobs/<job>``. Raise ``ValueError`` for a bad id."""
    if not _JOB_ID.fullmatch(job_id):
        raise ValueError(f"invalid job id: {job_id!r}")
    return task_dir(task_id, home) / "jobs" / job_id
