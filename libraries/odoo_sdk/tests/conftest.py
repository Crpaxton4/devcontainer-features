"""Fail the session when the tests import ``odoo_sdk`` from another checkout.

The repository's ``.venv`` is shared by every git worktree and carries an
editable install whose ``.pth`` file names one absolute path into the main
checkout. A pytest run launched from a worktree therefore used to import the
main checkout's ``odoo_sdk`` while collecting the worktree's tests — silently
validating source the run never touched (#860).

``pythonpath = ["src"]`` in ``[tool.pytest.ini_options]`` fixes the common case
by prepending this checkout's ``src/`` to ``sys.path``. This guard covers the
rest: an invocation that resolves a different rootdir, an inherited
``PYTHONPATH`` pointing elsewhere, or a stray non-editable install. A false
PASS on unimported source is indistinguishable from a real one, so the guard
fails the session rather than warning.
"""

from pathlib import Path

import pytest

EXPECTED_SRC = Path(__file__).resolve().parents[1] / "src"


@pytest.fixture(scope="session", autouse=True)
def _odoo_sdk_imported_from_this_checkout() -> None:
    """Assert ``odoo_sdk`` resolves under this checkout's ``src/``."""
    import odoo_sdk

    package_file = Path(odoo_sdk.__file__ or "").resolve()
    expected = EXPECTED_SRC.resolve()

    if expected in package_file.parents:
        return

    checkout = expected.parent
    pytest.exit(
        "odoo_sdk was imported from outside this checkout, so these tests "
        "would validate source this run never touched.\n"
        f"  imported from: {package_file}\n"
        f"  expected under: {expected}\n"
        "The shared .venv carries an editable install pinned to the main "
        "checkout, and every worktree resolves that same .venv.\n"
        f"Fix: run pytest from {checkout} (the ini file's pythonpath then "
        f'pins src/), or pass PYTHONPATH="{expected}" on the same command '
        "line as pytest.",
        returncode=1,
    )


@pytest.fixture(autouse=True)
def _no_ambient_odoo_dev_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hide the developer's real odoo-dev state dir from every test (#979).

    ``start_task`` now reads ``$ODOO_DEV_STATE_DIR/repo-map.json`` to resolve a
    base branch, and that variable is exported for real in this devcontainer
    (``/usr/local/share/odoo-dev``). Left in place, a test asserting the
    ``origin/HEAD`` fallback would pass or fail according to whichever projects
    the machine running it happens to have mapped. Tests that mean to exercise
    the map set the variable themselves, inside their own patch scope.
    """
    monkeypatch.delenv("ODOO_DEV_STATE_DIR", raising=False)
