"""Tests for the ``odoo-sdk discover`` and ``abort`` subcommands (issue #331)."""

import os
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import odoo_sdk.cli.__main__ as cli
from odoo_sdk.state import LocalStateClient
from odoo_sdk.state.db import _derive_repo_label
from odoo_sdk.utilities.timesheet import ANCHOR_NAME

ASSERT_GUARD = "odoo_sdk.cli.__main__.assert_odoo_devcontainer"
CMD_ASSERT_GUARD = "odoo_sdk.commands.builtin.abort_run.assert_odoo_devcontainer"


def _make_db(root: Path, project_hash: str, *, remote=None, run=None) -> Path:
    db_path = root / project_hash / "tasks.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = LocalStateClient(db_path=db_path)
    if remote is not None:
        db.set_setting("repo_remote_url", remote)
        db.set_setting("repo_label", _derive_repo_label(remote))
    if run is not None:
        db.create_run(*run)
    return db_path


class TestCmdDiscover(unittest.TestCase):
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

    def test_empty_root_message(self):
        self.assertIn("No task-tracker projects", self._run(["discover"]))

    def test_lists_runs_and_flags_stale(self):
        _make_db(
            self.root,
            "known",
            remote="git@github.com:o/repo.git",
            run=(1, "Recent Task", 10, "Proj", 50),
        )
        _make_db(self.root, "orphan", run=(2, "Orphaned Task", 10, "Proj", 60))
        # Age the orphan's run past the default staleness threshold.
        import sqlite3
        from datetime import datetime, timedelta, timezone

        conn = sqlite3.connect(str(self.root / "orphan" / "tasks.db"))
        old = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
        conn.execute("UPDATE task_runs SET started_at = ?", (old,))
        conn.commit()
        conn.close()

        output = self._run(["discover"])
        self.assertIn("o/repo", output)
        self.assertIn("Recent Task", output)
        self.assertIn("(unknown)", output)
        self.assertIn("Orphaned Task", output)
        self.assertIn("STALE", output)

    def test_local_only_skips_devcontainer_assert(self):
        with patch(ASSERT_GUARD) as mock_assert:
            self._run(["discover"])
        mock_assert.assert_not_called()

    def test_no_active_runs_and_note_rows_render(self):
        _make_db(self.root, "idle", remote="git@github.com:o/idle.git")
        bad = self.root / "corrupt" / "tasks.db"
        bad.parent.mkdir(parents=True)
        bad.write_bytes(b"not a database")
        output = self._run(["discover"])
        self.assertIn("(no active runs)", output)
        self.assertIn("skipped (unreadable)", output)


class TestCmdAbort(unittest.TestCase):
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

    def _client(self):
        client = MagicMock()

        def _execute(model, method, *args, **kwargs):
            if method == "read":
                return [{"id": args[0][0], "name": ANCHOR_NAME}]
            return True

        client.execute.side_effect = _execute
        return client

    def test_abort_closes_anchor_and_prints(self):
        _make_db(self.root, "orphan", run=(1, "Wedged", 10, "Proj", 50))
        run_id = LocalStateClient(
            db_path=self.root / "orphan" / "tasks.db"
        ).get_active_run(1).id
        client = self._client()
        out = StringIO()
        with (
            patch("sys.stdout", out),
            patch(ASSERT_GUARD),
            patch(CMD_ASSERT_GUARD),
            patch("odoo_sdk.cli.__main__.OdooClient", return_value=client),
            patch("sys.argv", ["odoo-sdk", "abort", "orphan", str(run_id)]),
        ):
            cli.main()
        output = out.getvalue()
        self.assertIn("Aborted run", output)
        self.assertIn("anchor closed", output)

    def test_abort_reports_already_stopped(self):
        db = LocalStateClient(
            db_path=_make_db(self.root, "orphan", run=(1, "Wedged", 10, "Proj", 50))
        )
        run_id = db.get_active_run(1).id
        db.stop_run(1)
        out = StringIO()
        with (
            patch("sys.stdout", out),
            patch(ASSERT_GUARD),
            patch(CMD_ASSERT_GUARD),
            patch("odoo_sdk.cli.__main__.OdooClient", return_value=MagicMock()),
            patch("sys.argv", ["odoo-sdk", "abort", "orphan", str(run_id)]),
        ):
            cli.main()
        self.assertIn("already stopped", out.getvalue())


if __name__ == "__main__":
    unittest.main()
