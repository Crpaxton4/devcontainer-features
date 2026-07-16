"""Tests for the ``odoo-sdk discover`` and ``abort`` subcommands (issues #331, #369).

Discovery and abort now operate on the one host-provisioned central tracker DB
(``<state-root>/tracker.db``) rather than per-repo ``tasks.db`` files.
"""

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import odoo_sdk.cli.__main__ as cli
from odoo_sdk.state import LocalStateClient
from odoo_sdk.state.db import tracker_db_path
from odoo_sdk.billing.timesheet import ANCHOR_NAME
from tests.support import provision_schema

ASSERT_GUARD = "odoo_sdk.cli.__main__.assert_odoo_devcontainer"
CMD_ASSERT_GUARD = "odoo_sdk.commands.builtin.abort_run.assert_odoo_devcontainer"


def _central_db(root: Path) -> LocalStateClient:
    """Provision (or reuse) the central ``<root>/tracker.db`` and return a client."""
    db_path = tracker_db_path(root)
    provision_schema(db_path)
    return LocalStateClient(db_path=db_path)


def _backdate_all(root: Path, hours: float) -> None:
    conn = sqlite3.connect(str(tracker_db_path(root)))
    old = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    conn.execute("UPDATE task_runs SET started_at = ?", (old,))
    conn.commit()
    conn.close()


class _EnvRoot(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._prev = os.environ.get("ODOO_TASK_TRACKER_DIR")
        os.environ["ODOO_TASK_TRACKER_DIR"] = str(self.root)

    def tearDown(self) -> None:
        if self._prev is None:
            os.environ.pop("ODOO_TASK_TRACKER_DIR", None)
        else:
            os.environ["ODOO_TASK_TRACKER_DIR"] = self._prev
        self._tmp.cleanup()

    def _run(self, argv) -> str:
        out = StringIO()
        with patch("sys.stdout", out), patch("sys.argv", ["odoo-sdk", *argv]):
            cli.main()
        return out.getvalue()


class TestCmdDiscover(_EnvRoot):
    def test_empty_db_message(self):
        _central_db(self.root)  # provisioned but no runs
        self.assertIn("No active runs", self._run(["discover"]))

    def test_missing_db_errors_hard(self):
        # The central DB is host-provisioned; a missing one is a hard error
        # naming the path, not a silent empty listing (#369, acceptance 4).
        err = StringIO()
        with (
            patch("sys.stderr", err),
            patch("sys.argv", ["odoo-sdk", "discover"]),
            self.assertRaises(SystemExit) as ctx,
        ):
            cli.main()
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn(str(tracker_db_path(self.root)), err.getvalue())

    def test_lists_runs_and_flags_stale(self):
        db = _central_db(self.root)
        db.create_run(1, "Recent Task", 10, "Proj", 50)
        db.create_run(2, "Orphaned Task", 10, "Proj", 60)
        _backdate_all(self.root, 72)  # age both past the default threshold
        output = self._run(["discover"])
        self.assertIn("Recent Task", output)
        self.assertIn("Orphaned Task", output)
        self.assertIn("Proj", output)
        self.assertIn("STALE", output)

    def test_local_only_skips_devcontainer_assert(self):
        _central_db(self.root)
        with patch(ASSERT_GUARD) as mock_assert:
            self._run(["discover"])
        mock_assert.assert_not_called()


class TestCmdAbort(_EnvRoot):
    def _client(self):
        client = MagicMock()

        def _execute(model, method, *args, **kwargs):
            if method == "read":
                return [{"id": args[0][0], "name": ANCHOR_NAME}]
            return True

        client.execute.side_effect = _execute
        return client

    def test_abort_closes_anchor_and_prints(self):
        db = _central_db(self.root)
        db.create_run(1, "Wedged", 10, "Proj", 50)
        run_id = db.get_active_run(1).id
        client = self._client()
        out = StringIO()
        with (
            patch("sys.stdout", out),
            patch(ASSERT_GUARD),
            patch(CMD_ASSERT_GUARD),
            patch("odoo_sdk.cli.__main__.OdooClient", return_value=client),
            patch("sys.argv", ["odoo-sdk", "abort", str(run_id)]),
        ):
            cli.main()
        output = out.getvalue()
        self.assertIn("Aborted run", output)
        self.assertIn("anchor closed", output)

    def test_abort_reports_already_stopped(self):
        db = _central_db(self.root)
        db.create_run(1, "Wedged", 10, "Proj", 50)
        run_id = db.get_active_run(1).id
        db.stop_run(1)
        out = StringIO()
        with (
            patch("sys.stdout", out),
            patch(ASSERT_GUARD),
            patch(CMD_ASSERT_GUARD),
            patch("odoo_sdk.cli.__main__.OdooClient", return_value=MagicMock()),
            patch("sys.argv", ["odoo-sdk", "abort", str(run_id)]),
        ):
            cli.main()
        self.assertIn("already stopped", out.getvalue())


if __name__ == "__main__":
    unittest.main()
