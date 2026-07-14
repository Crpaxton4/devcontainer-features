"""Tests for cross-project discovery and DB-recorded repo identity (issue #331).

These build temporary state-root trees of ``<hash>/tasks.db`` files directly
(via injected ``db_path``) so no git remote is needed, and cover the identity
settings written on self-resolved construction.
"""

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from odoo_sdk.state.db import LocalStateClient, _derive_repo_label
from odoo_sdk.state.discovery import _is_stale, discover_projects


def _backdate(db_path: Path, task_id: int, hours: float) -> None:
    """Rewrite a run's ``started_at`` to ``hours`` ago so it reads as stale."""
    ts = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "UPDATE task_runs SET started_at = ? WHERE task_id = ?", (ts, task_id)
    )
    conn.commit()
    conn.close()


def _make_db(
    root: Path,
    project_hash: str,
    *,
    remote: str = None,
    runs=(),
) -> Path:
    """Create ``<root>/<hash>/tasks.db`` with optional identity and runs.

    ``runs`` is an iterable of ``(task_id, task_name, backdate_hours)`` tuples;
    a non-zero ``backdate_hours`` ages the run so discovery flags it stale.
    """
    db_path = root / project_hash / "tasks.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = LocalStateClient(db_path=db_path)
    if remote is not None:
        db.set_setting("repo_remote_url", remote)
        db.set_setting("repo_label", _derive_repo_label(remote))
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
        self.assertEqual(
            _derive_repo_label("https://example.com/o/r"), "o/r"
        )


class TestIsStale(unittest.TestCase):
    def test_naive_started_at_treated_as_utc(self):
        now = datetime.now(timezone.utc)
        naive_old = (now - timedelta(hours=2)).replace(tzinfo=None)
        self.assertTrue(_is_stale(naive_old, now - timedelta(hours=1)))

    def test_aware_recent_not_stale(self):
        now = datetime.now(timezone.utc)
        self.assertFalse(_is_stale(now, now - timedelta(hours=1)))


class TestDiscoverProjects(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_empty_root_returns_empty_list(self):
        self.assertEqual(discover_projects(root=self.root), [])

    def test_missing_root_returns_empty_list(self):
        self.assertEqual(discover_projects(root=self.root / "nope"), [])

    def test_lists_multiple_projects_sorted_by_hash(self):
        _make_db(self.root, "aaa", remote="git@github.com:o/a.git")
        _make_db(self.root, "bbb", remote="git@github.com:o/b.git")
        hashes = [p["project_hash"] for p in discover_projects(root=self.root)]
        self.assertEqual(hashes, ["aaa", "bbb"])

    def test_stale_running_run_flagged(self):
        _make_db(
            self.root,
            "orphan",
            remote="git@github.com:o/orphan.git",
            runs=[(1, "Wedged", 48)],
        )
        [project] = discover_projects(root=self.root)
        self.assertTrue(project["stale"])
        run = project["active_runs"][0]
        self.assertTrue(run["stale"])
        self.assertEqual(run["run_id"], 1)
        self.assertEqual(run["task_name"], "Wedged")
        self.assertEqual(run["timesheet_id"], 10)
        self.assertEqual(run["state"], "RUNNING")

    def test_fresh_run_not_flagged(self):
        _make_db(
            self.root,
            "fresh",
            remote="git@github.com:o/fresh.git",
            runs=[(1, "Recent", 0)],
        )
        [project] = discover_projects(root=self.root)
        self.assertFalse(project["stale"])
        self.assertFalse(project["active_runs"][0]["stale"])

    def test_missing_identity_settings_report_unknown(self):
        _make_db(self.root, "legacy", runs=[(1, "Old", 1)])
        [project] = discover_projects(root=self.root)
        self.assertEqual(project["repo_label"], "(unknown)")
        self.assertIsNone(project["repo_remote_url"])

    def test_identity_settings_surface_when_present(self):
        _make_db(self.root, "known", remote="git@github.com:o/repo.git")
        [project] = discover_projects(root=self.root)
        self.assertEqual(project["repo_label"], "o/repo")
        self.assertEqual(project["repo_remote_url"], "git@github.com:o/repo.git")

    def test_corrupt_db_skipped_with_note(self):
        _make_db(self.root, "good", remote="git@github.com:o/g.git")
        bad = self.root / "corrupt" / "tasks.db"
        bad.parent.mkdir(parents=True)
        bad.write_bytes(b"this is not a sqlite database")
        projects = {p["project_hash"]: p for p in discover_projects(root=self.root)}
        self.assertIsNone(projects["good"]["note"])
        self.assertIn("skipped (unreadable)", projects["corrupt"]["note"])
        self.assertEqual(projects["corrupt"]["repo_label"], "(unknown)")
        self.assertEqual(projects["corrupt"]["active_runs"], [])

    def test_stopped_run_is_not_active(self):
        db_path = _make_db(
            self.root,
            "done",
            remote="git@github.com:o/d.git",
            runs=[(1, "Finished", 0)],
        )
        LocalStateClient(db_path=db_path).stop_run(1)
        [project] = discover_projects(root=self.root)
        self.assertEqual(project["active_runs"], [])

    def test_custom_threshold_respected(self):
        _make_db(
            self.root,
            "edge",
            remote="git@github.com:o/e.git",
            runs=[(1, "Aging", 5)],
        )
        # 5h old is stale under a 1h threshold but fresh under the 12h default.
        fresh = discover_projects(root=self.root)[0]
        self.assertFalse(fresh["active_runs"][0]["stale"])
        stale = discover_projects(root=self.root, stale_after_hours=1.0)[0]
        self.assertTrue(stale["active_runs"][0]["stale"])


class TestDiscoverRunsCommand(unittest.TestCase):
    """The builtin command wraps discover_projects for registry/MCP exposure."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_execute_delegates_to_discover_projects(self):
        from unittest.mock import MagicMock

        from odoo_sdk.commands.builtin.discover_runs import DiscoverRunsCommand

        _make_db(self.root, "hash", remote="git@github.com:o/r.git")
        with patch(
            "odoo_sdk.commands.builtin.discover_runs.discover_projects",
            return_value=[{"project_hash": "hash"}],
        ) as mock_discover:
            result = DiscoverRunsCommand(MagicMock()).execute(stale_after_hours=6.0)
        mock_discover.assert_called_once_with(stale_after_hours=6.0)
        self.assertEqual(result, [{"project_hash": "hash"}])


class TestSelfResolvedIdentity(unittest.TestCase):
    """Identity settings are written once on self-resolved init, never else."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self._tmp.name) / "hash"
        self.project_dir.mkdir(parents=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _self_resolved(self, remote: str) -> LocalStateClient:
        with patch(
            "odoo_sdk.state.db._resolve_project_identity",
            return_value=(self.project_dir, remote),
        ):
            return LocalStateClient()

    def test_identity_written_on_self_resolved_init(self):
        client = self._self_resolved("git@github.com:owner/repo.git")
        self.assertEqual(
            client.get_setting("repo_remote_url"), "git@github.com:owner/repo.git"
        )
        self.assertEqual(client.get_setting("repo_label"), "owner/repo")

    def test_identity_never_overwritten(self):
        self._self_resolved("git@github.com:owner/repo.git")
        # A second self-resolved open with a different remote must not clobber it.
        self._self_resolved("git@github.com:someone/else.git")
        reopened = LocalStateClient(db_path=self.project_dir / "tasks.db")
        self.assertEqual(reopened.get_setting("repo_label"), "owner/repo")
        self.assertEqual(
            reopened.get_setting("repo_remote_url"), "git@github.com:owner/repo.git"
        )

    def test_identity_not_written_for_injected_db_path(self):
        client = LocalStateClient(db_path=self.project_dir / "tasks.db")
        self.assertIsNone(client.get_setting("repo_remote_url"))
        self.assertIsNone(client.get_setting("repo_label"))


if __name__ == "__main__":
    unittest.main()
