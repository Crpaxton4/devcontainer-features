"""Tests for the user-writable default state root (issue #106) and the single
central tracker DB path resolution (issue #369).

The tracker state root must never fall back to a system path such as
``/usr/local/share`` that a non-root MCP user cannot write to. It resolves via:

1. ``ODOO_TASK_TRACKER_DIR`` env var (highest precedence).
2. ``$XDG_STATE_HOME/odoo-task-tracker`` when ``XDG_STATE_HOME`` is set.
3. ``~/.local/state/odoo-task-tracker`` otherwise.

The central DB is ``<state-root>/tracker.db`` — no git remote is consulted and no
directory is a per-repo hash, so ssh and https clones converge on one DB. The SDK
never creates that DB (it is host-provisioned and bind-mounted): a missing DB
raises :class:`TrackerStateMissingError` rather than being materialized empty.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from odoo_sdk.state.db import (
    LocalStateClient,
    _default_root,
    tracker_db_path,
)
from odoo_sdk.state.models import TrackerStateMissingError
from tests.support import provision_schema


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
            os.environ.pop("XDG_STATE_HOME", None)
            root = _default_root()
        self.assertEqual(root, Path("/home/user/.local/state/odoo-task-tracker"))
        self.assertNotIn("/usr/local/share", str(root))


class TestTrackerDbPathDefaultRoot(unittest.TestCase):
    def test_env_override_takes_precedence(self):
        with patch.dict(
            "os.environ",
            {
                "ODOO_TASK_TRACKER_DIR": "/tmp/tt-override",
                "XDG_STATE_HOME": "/home/user/.state",
            },
            clear=False,
        ):
            path = tracker_db_path()
        self.assertEqual(path, Path("/tmp/tt-override/tracker.db"))

    def test_resolves_under_xdg_when_no_override(self):
        with patch.dict(
            "os.environ", {"XDG_STATE_HOME": "/home/user/.state"}, clear=False
        ):
            os.environ.pop("ODOO_TASK_TRACKER_DIR", None)
            path = tracker_db_path()
        self.assertEqual(path, Path("/home/user/.state/odoo-task-tracker/tracker.db"))
        self.assertNotIn("/usr/local/share", str(path))

    def test_resolves_under_home_when_no_override_or_xdg(self):
        with (
            patch.dict("os.environ", {}, clear=False),
            patch("odoo_sdk.state.db.Path.home", return_value=Path("/home/user")),
        ):
            os.environ.pop("ODOO_TASK_TRACKER_DIR", None)
            os.environ.pop("XDG_STATE_HOME", None)
            path = tracker_db_path()
        self.assertEqual(
            path, Path("/home/user/.local/state/odoo-task-tracker/tracker.db")
        )
        self.assertNotIn("/usr/local/share", str(path))


class TestLocalStateClientNeverCreates(unittest.TestCase):
    def test_default_client_targets_central_db_without_creating_it(self):
        """``LocalStateClient()`` binds to ``<state-root>/tracker.db`` and, when
        the host has not provisioned it, raises rather than creating one."""
        with tempfile.TemporaryDirectory() as root, patch.dict(
            "os.environ", {"ODOO_TASK_TRACKER_DIR": root}, clear=False
        ):
            client = LocalStateClient()
            expected = Path(root) / "tracker.db"
            self.assertEqual(client._db_path, expected)
            with self.assertRaises(TrackerStateMissingError):
                client.get_all_active_runs()
            self.assertFalse(expected.exists())  # nothing was created

    def test_default_client_reads_a_host_provisioned_central_db(self):
        """Once the host has provisioned ``tracker.db``, the default client uses
        it directly (no per-repo hash directory)."""
        with tempfile.TemporaryDirectory() as root, patch.dict(
            "os.environ", {"ODOO_TASK_TRACKER_DIR": root}, clear=False
        ):
            provision_schema(Path(root) / "tracker.db")
            client = LocalStateClient()
            self.assertEqual(client.get_all_active_runs(), [])


if __name__ == "__main__":
    unittest.main()
