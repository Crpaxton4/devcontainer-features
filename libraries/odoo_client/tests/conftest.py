"""Fail the session when the tests import ``odoo_client`` from another checkout.

The repository's ``.venv`` is shared by every git worktree and carries an
editable install whose ``.pth`` file names one absolute path into the main
checkout. A pytest run launched from a worktree therefore used to import the
main checkout's ``odoo_client`` while collecting the worktree's tests — silently
validating source the run never touched (#860).

The guard fails the session when an invocation resolves a different rootdir,
an inherited ``PYTHONPATH`` points elsewhere, or a stray non-editable install
shadows the checkout. A false PASS on unimported source is indistinguishable
from a real one, so the guard fails the session rather than warning.
"""

from pathlib import Path

import pytest

EXPECTED_SRC = Path(__file__).resolve().parents[1] / "src"


@pytest.fixture(scope="session", autouse=True)
def _odoo_client_imported_from_this_checkout() -> None:
    """Assert ``odoo_client`` resolves under this checkout's ``src/``."""
    import odoo_client

    package_file = Path(odoo_client.__file__ or "").resolve()
    expected = EXPECTED_SRC.resolve()

    if expected in package_file.parents:
        return

    checkout = expected.parent
    pytest.exit(
        "odoo_client was imported from outside this checkout, so these tests "
        "would validate source this run never touched.\n"
        f"  imported from: {package_file}\n"
        f"  expected under: {expected}\n"
        "The shared .venv carries an editable install pinned to the main "
        "checkout, and every worktree resolves that same .venv.\n"
        f"Fix: run pytest from {checkout}, or pass "
        f'PYTHONPATH="{expected}" on the same command line as pytest.',
        returncode=1,
    )


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
