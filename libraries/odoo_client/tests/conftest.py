"""Shared pytest fixtures."""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_odoo_dev_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point ``ODOO_DEV_HOME`` at a fresh directory for every test.

    ``config.toml``, ``state.db`` and ``repo-map.json`` all live under that
    root, and the devcontainer exports a real one. A fresh ``tmp_path`` keeps
    every test off the developer's real state. Tests that need a specific
    layout write into the returned directory or set the variable themselves.
    """
    home = tmp_path / "odoo-dev-home"
    monkeypatch.setenv("ODOO_DEV_HOME", str(home))
    return home
