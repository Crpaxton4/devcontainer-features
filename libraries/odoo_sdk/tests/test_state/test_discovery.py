"""Tests for single-DB active-run discovery (issues #331, #369).

Discovery now queries the one host-provisioned central tracker DB
(``<state-root>/tracker.db``) rather than globbing per-repo ``tasks.db`` files.
These build a temporary central DB (via the shared schema-provisioning helper),
create runs in it, and assert the flat active-run report and stale flags.
"""

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from odoo_sdk.state.db import LocalStateClient, _derive_repo_label, tracker_db_path
from odoo_sdk.state.discovery import _is_stale, discover_runs
from odoo_sdk.state.models import TrackerStateMissingError
from tests.support import provision_schema


def _backdate(db_path: Path, task_id: int, hours: float) -> None:
    """Rewrite a run's ``started_at`` to ``hours`` ago so it reads as stale."""
    ts = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "UPDATE task_runs SET started_at = ? WHERE task_id = ?", (ts, task_id)
    )
    conn.commit()
    conn.close()


def _central_db(root: Path, *, runs=()) -> Path:
    """Provision ``<root>/tracker.db`` and populate it with ``runs``.

    ``runs`` is an iterable of ``(task_id, task_name, backdate_hours)`` tuples; a
    non-zero ``backdate_hours`` ages the run so discovery flags it stale.
    """
    db_path = tracker_db_path(root)
    provision_schema(db_path)
    db = LocalStateClient(db_path=db_path)
    for task_id, task_name, backdate_hours in runs:
        db.create_run(task_id, task_name, 10, "Proj", timesheet_id=task_id * 10)
        if backdate_hours:
            _backdate(db_path, task_id, backdate_hours)
    return db_path


class TestDeriveRepoLabel(unittest.TestCase):
    def test_ssh_form(self):
        self.assertEqual(
            _derive_repo_label("git@github.com:owner/repo.git"), "owner/repo"
        )

    def test_https_form(self):
        self.assertEqual(
            _derive_repo_label("https://github.com/owner/repo.git"), "owner/repo"
        )

    def test_https_without_git_suffix(self):
        self.assertEqual(_derive_repo_label("https://example.com/o/r"), "o/r")


class TestIsStale(unittest.TestCase):
    def test_naive_started_at_treated_as_utc(self):
        now = datetime.now(timezone.utc)
        naive_old = (now - timedelta(hours=2)).replace(tzinfo=None)
        self.assertTrue(_is_stale(naive_old, now - timedelta(hours=1)))

    def test_aware_recent_not_stale(self):
        now = datetime.now(timezone.utc)
        self.assertFalse(_is_stale(now, now - timedelta(hours=1)))


class TestDiscoverRuns(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_provisioned_db_with_no_runs_returns_empty_list(self):
        _central_db(self.root)
        self.assertEqual(discover_runs(root=self.root), [])

    def test_missing_db_raises_named_error(self):
        # The central DB is host-provisioned; discovery must not create one.
        with self.assertRaises(TrackerStateMissingError):
            discover_runs(root=self.root / "nope")

    def test_lists_active_runs_sorted_by_start(self):
        _central_db(
            self.root, runs=[(1, "First", 3), (2, "Second", 1)]
        )
        runs = discover_runs(root=self.root)
        # Oldest start first (task 1 backdated 3h, task 2 backdated 1h).
        self.assertEqual([r["task_id"] for r in runs], [1, 2])

    def test_stale_running_run_flagged(self):
        _central_db(self.root, runs=[(1, "Wedged", 48)])
        [run] = discover_runs(root=self.root)
        self.assertTrue(run["stale"])
        self.assertEqual(run["run_id"], 1)
        self.assertEqual(run["task_name"], "Wedged")
        self.assertEqual(run["project_name"], "Proj")
        self.assertEqual(run["timesheet_id"], 10)
        self.assertEqual(run["state"], "RUNNING")

    def test_fresh_run_not_flagged(self):
        _central_db(self.root, runs=[(1, "Recent", 0)])
        [run] = discover_runs(root=self.root)
        self.assertFalse(run["stale"])

    def test_stopped_run_is_not_active(self):
        db_path = _central_db(self.root, runs=[(1, "Finished", 0)])
        LocalStateClient(db_path=db_path).stop_run(1)
        self.assertEqual(discover_runs(root=self.root), [])

    def test_runs_for_two_repos_all_surface_in_one_db(self):
        # The whole point of the central DB: runs that used to live in separate
        # per-repo DBs now all appear together.
        _central_db(self.root, runs=[(1, "RepoA work", 1), (2, "RepoB work", 1)])
        task_ids = {r["task_id"] for r in discover_runs(root=self.root)}
        self.assertEqual(task_ids, {1, 2})

    def test_custom_threshold_respected(self):
        _central_db(self.root, runs=[(1, "Aging", 5)])
        # 5h old is fresh under the 12h default but stale under a 1h threshold.
        self.assertFalse(discover_runs(root=self.root)[0]["stale"])
        self.assertTrue(discover_runs(root=self.root, stale_after_hours=1.0)[0]["stale"])


class TestDiscoverRunsCommand(unittest.TestCase):
    """The builtin command wraps ``discover_runs`` for registry/MCP exposure."""

    def test_execute_delegates_to_discover_runs(self):
        from unittest.mock import MagicMock, patch

        from odoo_sdk.commands.builtin.discover_runs import DiscoverRunsCommand

        with patch(
            "odoo_sdk.commands.builtin.discover_runs.discover_runs",
            return_value=[{"run_id": 7}],
        ) as mock_discover:
            result = DiscoverRunsCommand(MagicMock()).execute(stale_after_hours=6.0)
        mock_discover.assert_called_once_with(stale_after_hours=6.0)
        self.assertEqual(result, [{"run_id": 7}])


if __name__ == "__main__":
    unittest.main()
