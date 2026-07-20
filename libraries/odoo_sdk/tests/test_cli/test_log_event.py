"""Tests for the ``odoo-sdk log-event`` subcommand.

Events land in the one host-provisioned central tracker DB
(``$ODOO_TASK_TRACKER_DIR/tracker.db``, #369) regardless of the cwd's repo. The
cwd's git remote only supplies the event's display ``repo`` label; a non-git cwd
logs fine with ``repo=""``. A missing central DB is a hard error, never a silent
skip or an auto-created empty DB.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import odoo_sdk.cli.__main__ as cli
from odoo_sdk.state import LocalStateClient as TaskStateDB
from odoo_sdk.state.db import tracker_db_path
from tests.support import provision_schema

ASSERT_GUARD = "odoo_sdk.cli.__main__.assert_odoo_devcontainer"


def _run(argv: list[str]) -> StringIO:
    """Invoke ``cli.main`` with ``argv``; return captured stderr."""
    stderr = StringIO()
    with (
        patch("sys.argv", ["odoo-sdk", *argv]),
        patch("sys.stderr", stderr),
        patch("sys.stdout", StringIO()),
    ):
        cli.main()
    return stderr


class TestLogEvent(unittest.TestCase):
    def setUp(self) -> None:
        self._cwd = os.getcwd()
        self._state = tempfile.mkdtemp()
        self._repo = tempfile.mkdtemp()
        subprocess.run(
            ["git", "init"], cwd=self._repo, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "remote", "add", "origin", "https://example.com/o/r.git"],
            cwd=self._repo,
            check=True,
            capture_output=True,
        )
        os.environ["ODOO_TASK_TRACKER_DIR"] = self._state
        # The central DB is host-provisioned; the SDK never creates it.
        provision_schema(tracker_db_path(self._state))
        os.chdir(self._repo)

    def tearDown(self) -> None:
        os.chdir(self._cwd)
        os.environ.pop("ODOO_TASK_TRACKER_DIR", None)
        shutil.rmtree(self._state, ignore_errors=True)
        shutil.rmtree(self._repo, ignore_errors=True)

    def test_happy_path_writes_event_row(self) -> None:
        _run(["log-event", "--source", "claude:SessionStart", "--subject", "hi"])
        events = TaskStateDB().get_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].source, "claude:SessionStart")
        self.assertEqual(events[0].subject, "hi")

    def test_repo_label_derived_from_cwd_remote(self) -> None:
        _run(["log-event", "--source", "claude:SessionStart"])
        events = TaskStateDB().get_events()
        self.assertEqual(events[0].repo, "o/r")

    def test_branch_derived_from_cwd_checkout(self) -> None:
        # #509: the branch is resolved on the live write path, not left empty.
        subprocess.run(
            ["git", "checkout", "-b", "feat/kiosk"],
            cwd=self._repo,
            check=True,
            capture_output=True,
        )
        _run(["log-event", "--source", "claude:SessionStart"])
        events = TaskStateDB().get_events()
        self.assertEqual(events[0].branch, "feat/kiosk")

    def test_repeatable_task_id(self) -> None:
        _run(
            [
                "log-event",
                "--source",
                "claude:PostToolUse",
                "--subject",
                "work",
                "--task-id",
                "1",
                "--task-id",
                "2",
            ]
        )
        events = TaskStateDB().get_events()
        self.assertEqual(events[0].task_ids, ["1", "2"])

    def test_payload_persisted(self) -> None:
        _run(
            [
                "log-event",
                "--source",
                "claude:Stop",
                "--payload",
                '{"tool": "Bash"}',
            ]
        )
        events = TaskStateDB().get_events()
        self.assertEqual(events[0].payload, {"tool": "Bash"})

    def test_bad_payload_exits_2(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            _run(["log-event", "--source", "claude:Stop", "--payload", "not json"])
        self.assertEqual(ctx.exception.code, 2)
        self.assertEqual(TaskStateDB().get_events(), [])

    def test_non_object_payload_exits_2(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            _run(["log-event", "--source", "claude:Stop", "--payload", "[1, 2]"])
        self.assertEqual(ctx.exception.code, 2)

    def test_bad_source_exits_2(self) -> None:
        stderr = StringIO()
        with self.assertRaises(SystemExit) as ctx:
            with patch("sys.stderr", stderr), patch("sys.stdout", StringIO()):
                with patch("sys.argv", ["odoo-sdk", "log-event", "--source", "bogus"]):
                    cli.main()
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("unknown event source 'bogus'", stderr.getvalue())

    def test_known_non_claude_source_allowed(self) -> None:
        _run(["log-event", "--source", "commit", "--subject", "c"])
        events = TaskStateDB().get_events()
        self.assertEqual(events[0].source, "commit")

    def test_bad_timestamp_exits_2(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            _run(
                [
                    "log-event",
                    "--source",
                    "claude:Stop",
                    "--timestamp",
                    "not-a-time",
                ]
            )
        self.assertEqual(ctx.exception.code, 2)

    def test_explicit_timestamp_used(self) -> None:
        _run(
            [
                "log-event",
                "--source",
                "claude:Stop",
                "--timestamp",
                "2026-01-02T03:04:05+00:00",
            ]
        )
        events = TaskStateDB().get_events()
        self.assertEqual(events[0].timestamp.isoformat(), "2026-01-02T03:04:05+00:00")

    def test_local_only_skips_devcontainer_assert(self) -> None:
        with patch(ASSERT_GUARD) as mock_assert:
            _run(["log-event", "--source", "claude:SessionStart"])
        mock_assert.assert_not_called()

    def test_attach_active_run_attaches_active_task_ids(self) -> None:
        db = TaskStateDB()
        db.create_run(101, "Task A", 1, "Proj")
        db.create_run(202, "Task B", 1, "Proj")
        _run(["log-event", "--source", "claude:PreToolUse", "--attach-active-run"])
        events = TaskStateDB().get_events()
        self.assertEqual(sorted(events[0].task_ids), ["101", "202"])

    def test_attach_active_run_no_active_runs_yields_empty(self) -> None:
        _run(["log-event", "--source", "claude:PreToolUse", "--attach-active-run"])
        events = TaskStateDB().get_events()
        self.assertEqual(events[0].task_ids, [])

    def test_explicit_task_id_overrides_attach_active_run(self) -> None:
        db = TaskStateDB()
        db.create_run(101, "Task A", 1, "Proj")
        _run(
            [
                "log-event",
                "--source",
                "claude:PreToolUse",
                "--attach-active-run",
                "--task-id",
                "999",
            ]
        )
        events = TaskStateDB().get_events()
        self.assertEqual(events[0].task_ids, ["999"])

    def test_no_attach_flag_leaves_task_ids_empty(self) -> None:
        # The attribution policy moved into LogEventCommand (#507), but the flag
        # still selects it: without --attach-active-run this subcommand keeps
        # its documented default of leaving the event untargeted, so the hook
        # shim's contract is unchanged.
        db = TaskStateDB()
        db.create_run(101, "Task A", 1, "Proj")
        _run(["log-event", "--source", "claude:PreToolUse"])
        events = TaskStateDB().get_events()
        self.assertEqual(events[0].task_ids, [])


class TestLogEventNonGitRepo(unittest.TestCase):
    """A non-git cwd is no longer an error: the repo no longer selects the DB, so
    the event logs fine into the central DB with an empty ``repo`` label (#369)."""

    def setUp(self) -> None:
        self._cwd = os.getcwd()
        self._state = tempfile.mkdtemp()
        self._nongit = tempfile.mkdtemp()
        os.environ["ODOO_TASK_TRACKER_DIR"] = self._state
        provision_schema(tracker_db_path(self._state))
        os.chdir(self._nongit)

    def tearDown(self) -> None:
        os.chdir(self._cwd)
        os.environ.pop("ODOO_TASK_TRACKER_DIR", None)
        shutil.rmtree(self._state, ignore_errors=True)
        shutil.rmtree(self._nongit, ignore_errors=True)

    def test_non_git_cwd_logs_event_with_empty_repo(self) -> None:
        _run(["log-event", "--source", "claude:SessionStart"])
        events = TaskStateDB().get_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].repo, "")
        # Branch resolution is best-effort on the same terms (#509): no repo
        # means no branch, not a failed write.
        self.assertEqual(events[0].branch, "")


class TestLogEventMissingDb(unittest.TestCase):
    """With no host-provisioned central DB, log-event fails hard (#369, accept. 4)."""

    def setUp(self) -> None:
        self._cwd = os.getcwd()
        self._state = tempfile.mkdtemp()  # empty: no tracker.db provisioned
        self._nongit = tempfile.mkdtemp()
        os.environ["ODOO_TASK_TRACKER_DIR"] = self._state
        os.chdir(self._nongit)

    def tearDown(self) -> None:
        os.chdir(self._cwd)
        os.environ.pop("ODOO_TASK_TRACKER_DIR", None)
        shutil.rmtree(self._state, ignore_errors=True)
        shutil.rmtree(self._nongit, ignore_errors=True)

    def test_missing_db_exits_1_and_names_path(self) -> None:
        stderr = StringIO()
        with self.assertRaises(SystemExit) as ctx:
            with patch("sys.stderr", stderr), patch("sys.stdout", StringIO()):
                with patch(
                    "sys.argv",
                    ["odoo-sdk", "log-event", "--source", "claude:SessionStart"],
                ):
                    cli.main()
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn(str(tracker_db_path(self._state)), stderr.getvalue())
        # The SDK must NOT have created a DB as a side effect.
        self.assertFalse(tracker_db_path(self._state).exists())
        self.assertEqual(list(Path(self._state).rglob("*.db")), [])


if __name__ == "__main__":
    unittest.main()
