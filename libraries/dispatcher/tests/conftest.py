"""Shared fixtures."""

import pytest

from dispatcher.settings import Settings


@pytest.fixture(autouse=True)
def state_root(tmp_path, monkeypatch):
    root = tmp_path / "state"
    monkeypatch.setenv("ODOO_DISPATCH_STATE_DIR", str(root))
    monkeypatch.delenv("COMPOSE_PROJECT_NAME", raising=False)
    return root


@pytest.fixture
def settings(tmp_path):
    invariants = tmp_path / "unattended-invariants.md"
    invariants.write_text("Standing line.\nNever ask.\n", "utf-8")
    return Settings(
        repos_dir=tmp_path / "repos",
        config_root=tmp_path / "central",
        owner="acme",
        plugin_root=tmp_path / "plugin",
        invariants_file=str(invariants),
        invariants=invariants.read_text("utf-8"),
        agent="odoo-dev:odoo-dev-implementer",
        min_available_gb=1,
        settle_s=0,
        poll_s=0.01,
        deadline_s=60,
        claude_home=tmp_path / "claude",
    )
