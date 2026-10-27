"""Shared fixtures."""

import pytest

from dispatcher.settings import Settings


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """The one state root of the suite: a temp ``ODOO_DEV_HOME``."""
    root = tmp_path / "odoo-dev"
    monkeypatch.setenv("ODOO_DEV_HOME", str(root))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("COMPOSE_PROJECT_NAME", raising=False)
    return root


@pytest.fixture
def settings(tmp_path, home):
    invariants = tmp_path / "unattended-invariants.md"
    invariants.write_text("Standing line.\nNever ask.\n", "utf-8")
    for repo in ("qoc", "a", "b", "c", "x"):
        config = (
            tmp_path / "central" / "github.com" / "acme" / repo / ".devcontainer"
        ) / "devcontainer.json"
        config.parent.mkdir(parents=True)
        config.write_text("{}", "utf-8")
    return Settings(
        home=home,
        repos_dir=tmp_path / "repos",
        config_root=tmp_path / "central",
        owner="acme",
        plugin_root=tmp_path / "plugin",
        invariants_file=str(invariants),
        invariants=invariants.read_text("utf-8"),
        entry="/odoo-dev:implement",
        min_available_gb=1,
        settle_s=0,
        poll_s=0.01,
        deadline_s=60,
        admit_timeout_s=1800,
        claude_home=tmp_path / "claude",
    )
