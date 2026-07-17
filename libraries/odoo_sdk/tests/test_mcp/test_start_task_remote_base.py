"""Real-git regression test for #454: fork task branches from the remote tip.

Unlike the mocked-``subprocess`` unit tests in ``test_tools`` (which assert the
*argv* the helper emits), this drives ``_create_task_branch`` against a genuine
temporary git repository whose local base branch is deliberately behind its
``origin`` counterpart. It proves the observable outcome: after the fix the new
task branch lands on ``origin/<base>`` (containing the recently-merged file),
not the stale local base commit that lacked it. This is the exact wrong-base
scenario from the issue (a long-lived local pointer drifted behind origin).
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from odoo_sdk.mcp.tools.start_task import _create_task_branch


def _git(cwd: Path, *args: str) -> str:
    """Run ``git`` in ``cwd`` with a fixed identity, returning stripped stdout."""
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    # Neutralise any global commit-msg / pre-commit hooks (commitlint, gitleaks)
    # the host may install via core.hooksPath — the throwaway fixture commits use
    # plain messages and must not be gated by the repo's Conventional-Commits rule.
    result = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


class TestForksFromRemoteTip(unittest.TestCase):
    """#454: a new task branch forks from the fetched ``origin/<base>`` tip."""

    def setUp(self):
        self._prev_cwd = Path.cwd()
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)

        # A bare repo acts as the shared "origin".
        self.origin = root / "origin.git"
        _git(root, "init", "--bare", "-b", "main", str(self.origin))

        # A working clone that will fall behind origin.
        self.local = root / "local"
        _git(root, "clone", str(self.origin), str(self.local))
        (self.local / "base.txt").write_text("base\n")
        _git(self.local, "add", "base.txt")
        _git(self.local, "commit", "-m", "initial")
        _git(self.local, "push", "origin", "main")

        # Advance origin/main via a *separate* clone, then never pull locally —
        # the local ``main`` pointer is now stale (missing feature_module.py).
        advancer = root / "advancer"
        _git(root, "clone", str(self.origin), str(advancer))
        (advancer / "feature_module.py").write_text("# merged work\n")
        _git(advancer, "add", "feature_module.py")
        _git(advancer, "commit", "-m", "add feature module")
        _git(advancer, "push", "origin", "main")
        self.remote_tip = _git(advancer, "rev-parse", "HEAD")
        self.stale_local_tip = _git(self.local, "rev-parse", "main")

        os.chdir(self.local)

    def tearDown(self):
        os.chdir(self._prev_cwd)
        self._tmp.cleanup()

    def test_new_branch_created_from_remote_tip_not_stale_local(self):
        self.assertNotEqual(
            self.remote_tip, self.stale_local_tip, "origin/main must be ahead of local"
        )

        created = _create_task_branch("10#fix", "main")

        self.assertTrue(created, "a fresh branch must be reported as created")
        self.assertEqual(_git(self.local, "rev-parse", "--abbrev-ref", "HEAD"), "10#fix")
        # The new branch sits on the remote tip, so the merged file is present.
        self.assertEqual(_git(self.local, "rev-parse", "HEAD"), self.remote_tip)
        self.assertTrue(
            (self.local / "feature_module.py").exists(),
            "branch forked from origin/main must contain the merged module",
        )

    def test_untracked_files_survive_the_fork(self):
        # The fetch/fork must leave untracked files in place (only their content
        # matters here; the stash+pop cycle re-creates them, which is separate).
        (self.local / "scratch.txt").write_text("keep me\n")

        _create_task_branch("10#fix", "main")

        self.assertTrue((self.local / "scratch.txt").exists())
        self.assertEqual((self.local / "scratch.txt").read_text(), "keep me\n")


if __name__ == "__main__":
    unittest.main()
