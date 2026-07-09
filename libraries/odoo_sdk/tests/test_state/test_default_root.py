"""Tests for the user-writable default state root (issue #106).

The tracker state root must never fall back to a system path such as
``/usr/local/share`` that a non-root MCP user cannot write to. It resolves via:

1. ``ODOO_TASK_TRACKER_DIR`` env var (highest precedence).
2. ``$XDG_STATE_HOME/odoo-task-tracker`` when ``XDG_STATE_HOME`` is set.
3. ``~/.local/state/odoo-task-tracker`` otherwise.
"""

import unittest
from pathlib import Path
from unittest.mock import patch

from odoo_sdk.state.db import (
    LocalStateClient,
    _default_root,
    _get_project_dir,
)


class TestDefaultRoot(unittest.TestCase):
    def test_uses_xdg_state_home_when_set(self):
        with patch.dict(
            "os.environ", {"XDG_STATE_HOME": "/home/user/.state"}, clear=False
        ):
            root = _default_root()
        self.assertEqual(root, Path("/home/user/.state/odoo-task-tracker"))
        self.assertNotIn("/usr/local/share", str(root))

    def test_falls_back_to_home_local_state(self):
        with (
            patch.dict("os.environ", {}, clear=False),
            patch("odoo_sdk.state.db.Path.home", return_value=Path("/home/user")),
        ):
            # Ensure XDG_STATE_HOME is unset for this branch.
            import os

            os.environ.pop("XDG_STATE_HOME", None)
            root = _default_root()
        self.assertEqual(root, Path("/home/user/.local/state/odoo-task-tracker"))
        self.assertNotIn("/usr/local/share", str(root))


class TestGetProjectDirDefaultRoot(unittest.TestCase):
    def _mock_git(self):
        return type("R", (), {"stdout": "git@github.com:org/repo.git\n"})()

    def test_env_override_takes_precedence(self):
        with (
            patch("odoo_sdk.state.db.subprocess.run", return_value=self._mock_git()),
            patch.dict(
                "os.environ",
                {
                    "ODOO_TASK_TRACKER_DIR": "/tmp/tt-override",
                    "XDG_STATE_HOME": "/home/user/.state",
                },
                clear=False,
            ),
            patch("odoo_sdk.state.db.Path.mkdir"),
        ):
            project_dir = _get_project_dir()
        self.assertTrue(str(project_dir).startswith("/tmp/tt-override"))

    def test_resolves_under_xdg_when_no_override(self):
        with (
            patch("odoo_sdk.state.db.subprocess.run", return_value=self._mock_git()),
            patch.dict(
                "os.environ", {"XDG_STATE_HOME": "/home/user/.state"}, clear=False
            ),
            patch("odoo_sdk.state.db.Path.mkdir"),
        ):
            import os

            os.environ.pop("ODOO_TASK_TRACKER_DIR", None)
            project_dir = _get_project_dir()
        self.assertTrue(
            str(project_dir).startswith("/home/user/.state/odoo-task-tracker")
        )
        self.assertNotIn("/usr/local/share", str(project_dir))

    def test_resolves_under_home_when_no_override_or_xdg(self):
        with (
            patch("odoo_sdk.state.db.subprocess.run", return_value=self._mock_git()),
            patch.dict("os.environ", {}, clear=False),
            patch("odoo_sdk.state.db.Path.home", return_value=Path("/home/user")),
            patch("odoo_sdk.state.db.Path.mkdir"),
        ):
            import os

            os.environ.pop("ODOO_TASK_TRACKER_DIR", None)
            os.environ.pop("XDG_STATE_HOME", None)
            project_dir = _get_project_dir()
        self.assertTrue(
            str(project_dir).startswith("/home/user/.local/state/odoo-task-tracker")
        )
        self.assertNotIn("/usr/local/share", str(project_dir))


class TestLocalStateClientCreatesDir(unittest.TestCase):
    def test_creates_dir_and_db_in_tmp_home(self):
        import os

        with (
            patch(
                "odoo_sdk.state.db.subprocess.run",
                return_value=type(
                    "R", (), {"stdout": "git@github.com:org/repo.git\n"}
                )(),
            ),
            patch.dict("os.environ", {}, clear=False),
        ):
            with tempfile_home() as home:
                os.environ.pop("ODOO_TASK_TRACKER_DIR", None)
                os.environ.pop("XDG_STATE_HOME", None)
                os.environ["HOME"] = str(home)
                client = LocalStateClient()
                # The db file and its parent project dir must have been created
                # under the tmp HOME, not under a system path.
                self.assertTrue(client._db_path.exists())
                self.assertTrue(
                    str(client._db_path).startswith(
                        str(home / ".local" / "state" / "odoo-task-tracker")
                    )
                )

    def test_creates_dir_and_db_under_xdg_state_home(self):
        import os

        with (
            patch(
                "odoo_sdk.state.db.subprocess.run",
                return_value=type(
                    "R", (), {"stdout": "git@github.com:org/repo.git\n"}
                )(),
            ),
            patch.dict("os.environ", {}, clear=False),
        ):
            with tempfile_home() as home:
                xdg = home / "xdg-state"
                os.environ.pop("ODOO_TASK_TRACKER_DIR", None)
                os.environ["XDG_STATE_HOME"] = str(xdg)
                client = LocalStateClient()
                self.assertTrue(client._db_path.exists())
                self.assertTrue(
                    str(client._db_path).startswith(str(xdg / "odoo-task-tracker"))
                )


class tempfile_home:
    """Context manager yielding a temporary directory usable as ``HOME``."""

    def __enter__(self) -> Path:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        return Path(self._tmp.name)

    def __exit__(self, *exc) -> None:
        self._tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
