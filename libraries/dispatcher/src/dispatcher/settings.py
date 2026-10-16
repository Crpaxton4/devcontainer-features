"""Dispatcher settings, read from the environment once at startup.

Variables (all optional unless marked required):

``DISPATCH_REPOS_DIR`` (or ``REPOS_DIR``)  required
    Host directory holding the customer repos: ``<repos>/<repo>`` is the
    ``--workspace-folder``.
``DISPATCH_CONFIG_ROOT``  required
    Root of the central devcontainer config tree. The config for a repo is
    ``<root>/github.com/<owner>/<repo>/.devcontainer/devcontainer.json``.
``DISPATCH_REPO_OWNER``  required
    The ``<owner>`` path component above.
``DISPATCH_PLUGIN_ROOT``
    Root of the odoo-dev plugin (default ``$CLAUDE_PLUGIN_ROOT``, then the
    checkout this package lives in).
``DISPATCH_INVARIANTS_FILE``
    A HOST path. The dispatcher reads the file once per run and inlines its
    text with ``--append-system-prompt``, as it inlines the brief. A missing or
    unreadable file is a settings error (exit 2). Default
    ``<plugin root>/prompts/unattended-invariants.md``.
``DISPATCH_AGENT``
    The in-container implementation agent (default ``odoo-dev:odoo-dev-implementer``).
``MIN_FREE_GB``
    Admission threshold on available memory, in GB (default 6, the same knob
    ``stack-ensure.sh`` evicts on).
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
"""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DEFAULT_AGENT = "odoo-dev:odoo-dev-implementer"
DEFAULT_ADDITIONAL_FEATURES = (
    '{"ghcr.io/crpaxton4/devcontainer-features/personal-features":{}}'
)
INVARIANTS_RELATIVE = Path("prompts") / "unattended-invariants.md"
WORKSPACE_TARGET = "/mnt/extra-addons"
ARTIFACTS_TARGET = "/artifacts"


class SettingsError(RuntimeError):
    """A required setting is missing or malformed."""


def read_invariants(path: str) -> str:
    """The text of the invariants file, read on the host.

    Raises :class:`SettingsError` naming the path when it cannot be read, so a
    missing file stops the run at startup and is never a dead letter.
    """
    try:
        return Path(path).read_text("utf-8")
    except OSError as exc:
        raise SettingsError(f"cannot read the invariants file {path}: {exc}") from exc


def normalize_project(repo: str) -> str:
    """The compose project name for a repo, as compose derives it."""
    return re.sub(r"[^a-z0-9_-]", "", repo.lower())


@dataclass(frozen=True)
class Settings:
    repos_dir: Path
    config_root: Path
    owner: str
    plugin_root: Path
    invariants_file: str
    agent: str
    min_available_gb: float
    settle_s: float
    poll_s: float
    deadline_s: float
    claude_home: Path
    additional_features: str = DEFAULT_ADDITIONAL_FEATURES
    invariants: str = ""  # the text of ``invariants_file``, read once by from_env

    def workspace(self, repo: str) -> Path:
        """The ``--workspace-folder`` of a repo."""
        return self.repos_dir / repo

    def config(self, repo: str) -> Path:
        """The central ``devcontainer.json`` of a repo (``--config``)."""
        return (
            self.config_root
            / "github.com"
            / self.owner
            / repo
            / ".devcontainer"
            / "devcontainer.json"
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
                ("DISPATCH_REPO_OWNER", env.get("DISPATCH_REPO_OWNER")),
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
            }
        except ValueError as exc:
            raise SettingsError(f"a numeric setting is malformed: {exc}") from exc
        invariants_file = env.get("DISPATCH_INVARIANTS_FILE") or str(
            plugin_root / INVARIANTS_RELATIVE
        )
        return cls(
            repos_dir=Path(repos),
            config_root=Path(env["DISPATCH_CONFIG_ROOT"]),
            owner=env["DISPATCH_REPO_OWNER"],
            plugin_root=plugin_root,
            invariants_file=invariants_file,
            invariants=read_invariants(invariants_file),
            agent=env.get("DISPATCH_AGENT") or DEFAULT_AGENT,
            claude_home=Path(claude_home),
            additional_features=env.get("DISPATCH_ADDITIONAL_FEATURES")
            or DEFAULT_ADDITIONAL_FEATURES,
            **numbers,
        )
