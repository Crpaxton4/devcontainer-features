"""Dispatcher settings, read from the environment once at startup.

Variables (all optional unless marked required):

``DISPATCH_REPOS_DIR`` (or ``REPOS_DIR``)  required
    Host directory holding the customer repos: ``<repos>/<repo>`` is the
    ``--workspace-folder``.
``DISPATCH_CONFIG_ROOT``  required
    Root of the central devcontainer config tree. The config for a repo is
    ``<root>/github.com/<owner>/<repo>/.devcontainer/devcontainer.json``.
``DISPATCH_REPO_OWNER``
    Optional tie-breaker for the ``<owner>`` path component above. The owner is
    resolved per repo: the single match of
    ``<root>/github.com/*/<repo>/.devcontainer/devcontainer.json``. When this is
    set and its own path exists, that path wins; otherwise the glob decides.
``DISPATCH_PLUGIN_ROOT``
    Root of the odoo-dev plugin (default ``$CLAUDE_PLUGIN_ROOT``, then the
    checkout this package lives in).
``DISPATCH_INVARIANTS_FILE``
    A HOST path. The dispatcher reads the file once per run and inlines its
    text with ``--append-system-prompt``, as it inlines the brief. A missing or
    unreadable file is a settings error (exit 2). Default
    ``<plugin root>/prompts/unattended-invariants.md``.
``DISPATCH_ENTRY``
    The slash command (or ``/plugin:workflow``) that starts the session; the
    task id and the brief follow it in the prompt (default
    ``/odoo-dev:implement``). It replaces ``DISPATCH_AGENT``: a run with
    ``DISPATCH_AGENT`` still set is a settings error, so a stale configuration
    fails loudly and never runs under the wrong entry.
``MIN_FREE_GB``
    Admission threshold on available memory, in GB (default 6, the same knob
    ``stack-ensure.sh`` evicts on).
``DISPATCH_ADMIT_TIMEOUT_S``
    How long the run waits for memory with nothing admitted and no job running
    (default 1800). When that time passes while jobs are pending, the run ends
    with the jobs still ``PENDING`` and exit 1, and stderr names the threshold
    and the last measured figure. The dispatcher never evicts a stack: stopping
    an idle editor stack is a manual step.
``DISPATCH_SETTLE_S``
    Seconds to let the machine settle after admitting a job (default 5).
``DISPATCH_POLL_S``
    Seconds between admission attempts while waiting (default 15).
``DISPATCH_DEADLINE_S``
    Wall-clock deadlock guard per job (default 21600, six hours). Not a budget.
``DISPATCH_CLAUDE_HOME``
    The host ``.claude`` directory (default ``$CLAUDE_CONFIG_DIR``, then
    ``~/.claude``), where session transcripts land.
``DISPATCH_ADDITIONAL_FEATURES``
    JSON passed to ``devcontainer up --additional-features`` (default the
    personal-features Feature, never empty). The Feature provides ``claude``,
    ``/usr/local/share/claude-home`` and ``/usr/local/share/odoo-dev`` in a
    container that the dispatcher creates cold.
``DISPATCH_ARTIFACTS_DIR``
    The bundle root on the HOST (default ``~/.config/odoo-dev/dispatch/artifacts``),
    read by :func:`odoo_sdk.state.dispatch_state.artifacts_root`. The container
    sees it at ``/usr/local/share/odoo-dev/dispatch/artifacts`` through the bind
    that the Feature makes of ``~/.config/odoo-dev``. A value outside that
    directory is a settings error (exit 2), because the container cannot see it.
    A running container keeps the mounts it was created with, so the dispatcher
    passes no ``--mount`` of its own.
"""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from odoo_sdk.state.dispatch_state import DISPATCH_ARTIFACTS_DIR_ENV, odoo_dev_dir

DEFAULT_ENTRY = "/odoo-dev:implement"
#: The entries that a submitted job may name for itself (its ``entry`` key).
ALLOWED_ENTRIES = (DEFAULT_ENTRY, "/odoo-dev:upgrade-implement")
DEFAULT_ADDITIONAL_FEATURES = (
    '{"ghcr.io/crpaxton4/devcontainer-features/personal-features":{}}'
)
INVARIANTS_RELATIVE = Path("prompts") / "unattended-invariants.md"
WORKSPACE_TARGET = "/mnt/extra-addons"
#: Where the personal-features Feature binds the host ``~/.config/odoo-dev``.
ODOO_DEV_MOUNT_TARGET = "/usr/local/share/odoo-dev"
#: The bundle root as the container sees it (under the Feature's odoo-dev bind).
ARTIFACTS_TARGET = f"{ODOO_DEV_MOUNT_TARGET}/dispatch/artifacts"


class SettingsError(RuntimeError):
    """A required setting is missing or malformed."""


class ConfigResolutionError(RuntimeError):
    """No single central config exists for a repo; ``detail`` says why.

    This is a per-job error, not a settings error: the engine dead-letters the
    one job and the dispatcher carries on.
    """

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


def read_invariants(path: str) -> str:
    """The text of the invariants file, read on the host.

    Raises :class:`SettingsError` naming the path when it cannot be read, so a
    missing file stops the run at startup and is never a dead letter.
    """
    try:
        return Path(path).read_text("utf-8")
    except OSError as exc:
        raise SettingsError(f"cannot read the invariants file {path}: {exc}") from exc


def check_artifacts_dir(env: Mapping[str, str]) -> None:
    """Raise :class:`SettingsError` when ``DISPATCH_ARTIFACTS_DIR`` is not visible.

    The container sees the host only through the Feature's bind of
    ``~/.config/odoo-dev``, so the bundle root must sit under that directory.
    """
    value = env.get(DISPATCH_ARTIFACTS_DIR_ENV)
    if not value:
        return
    wanted = Path(value).expanduser().resolve()
    if not wanted.is_relative_to(odoo_dev_dir().resolve()):
        raise SettingsError(
            f"{DISPATCH_ARTIFACTS_DIR_ENV}={value} is not under {odoo_dev_dir()}, "
            f"the directory that the Feature binds at {ODOO_DEV_MOUNT_TARGET}; "
            "the container could not see the bundles"
        )


def check_no_stale_agent(env: Mapping[str, str]) -> None:
    """Raise :class:`SettingsError` when the retired ``DISPATCH_AGENT`` is set."""
    if env.get("DISPATCH_AGENT"):
        raise SettingsError(
            "DISPATCH_AGENT is retired: the session now starts at a slash "
            "command, not at an agent. Unset it, and set DISPATCH_ENTRY "
            f"(default {DEFAULT_ENTRY}) to change the entry"
        )


def normalize_project(repo: str) -> str:
    """The compose project name for a repo, as compose derives it."""
    return re.sub(r"[^a-z0-9_-]", "", repo.lower())


@dataclass(frozen=True)
class Settings:
    repos_dir: Path
    config_root: Path
    plugin_root: Path
    invariants_file: str
    entry: str
    min_available_gb: float
    settle_s: float
    poll_s: float
    deadline_s: float
    admit_timeout_s: float
    claude_home: Path
    additional_features: str = DEFAULT_ADDITIONAL_FEATURES
    invariants: str = ""  # the text of ``invariants_file``, read once by from_env
    owner: str = ""  # optional tie-breaker; "" means resolve by glob only

    def workspace(self, repo: str) -> Path:
        """The ``--workspace-folder`` of a repo."""
        return self.repos_dir / repo

    def config(self, repo: str) -> Path:
        """The central ``devcontainer.json`` of a repo (``--config``).

        Raises :class:`ConfigResolutionError` when no single config exists.
        """
        tail = Path(repo) / ".devcontainer" / "devcontainer.json"
        github = self.config_root / "github.com"
        if self.owner:
            preferred = github / self.owner / tail
            if preferred.is_file():
                return preferred
        pattern = f"*/{tail}"
        found = sorted(github.glob(pattern))
        if len(found) == 1:
            return found[0]
        if not found:
            raise ConfigResolutionError(
                f"no devcontainer config for repo {repo}: nothing matches "
                f"{github / pattern}"
            )
        raise ConfigResolutionError(
            f"more than one devcontainer config for repo {repo}: "
            + ", ".join(str(p) for p in found)
            + "; set DISPATCH_REPO_OWNER to choose one"
        )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if env is None else env
        repos = env.get("DISPATCH_REPOS_DIR") or env.get("REPOS_DIR")
        missing = [
            name
            for name, value in (
                ("DISPATCH_REPOS_DIR", repos),
                ("DISPATCH_CONFIG_ROOT", env.get("DISPATCH_CONFIG_ROOT")),
            )
            if not value
        ]
        if missing:
            raise SettingsError("required settings not set: " + ", ".join(missing))
        plugin_root = Path(
            env.get("DISPATCH_PLUGIN_ROOT")
            or env.get("CLAUDE_PLUGIN_ROOT")
            or Path(__file__).resolve().parents[4] / "plugins" / "odoo-dev"
        )
        claude_home = (
            env.get("DISPATCH_CLAUDE_HOME")
            or env.get("CLAUDE_CONFIG_DIR")
            or str(Path.home() / ".claude")
        )
        try:
            numbers = {
                "min_available_gb": float(env.get("MIN_FREE_GB", 6)),
                "settle_s": float(env.get("DISPATCH_SETTLE_S", 5)),
                "poll_s": float(env.get("DISPATCH_POLL_S", 15)),
                "deadline_s": float(env.get("DISPATCH_DEADLINE_S", 21600)),
                "admit_timeout_s": float(env.get("DISPATCH_ADMIT_TIMEOUT_S", 1800)),
            }
        except ValueError as exc:
            raise SettingsError(f"a numeric setting is malformed: {exc}") from exc
        check_artifacts_dir(env)
        check_no_stale_agent(env)
        invariants_file = env.get("DISPATCH_INVARIANTS_FILE") or str(
            plugin_root / INVARIANTS_RELATIVE
        )
        return cls(
            repos_dir=Path(repos),
            config_root=Path(env["DISPATCH_CONFIG_ROOT"]),
            owner=env.get("DISPATCH_REPO_OWNER", ""),
            plugin_root=plugin_root,
            invariants_file=invariants_file,
            invariants=read_invariants(invariants_file),
            entry=env.get("DISPATCH_ENTRY") or DEFAULT_ENTRY,
            claude_home=Path(claude_home),
            additional_features=env.get("DISPATCH_ADDITIONAL_FEATURES")
            or DEFAULT_ADDITIONAL_FEATURES,
            **numbers,
        )
