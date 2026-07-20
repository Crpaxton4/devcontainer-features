"""Robustness tests for ``start_task`` branch setup (#478, #541, #542).

Four independent failure modes are covered here, each previously observable as
"my branch/work vanished":

* #541 — ``_setup_task_branch`` ran *outside* the try/except that calls
  ``_rollback_task_branch``, so a mid-setup failure stranded a dangling branch.
* #541 — the ``CalledProcessError`` git raises was absent from
  ``_BOUNDARY_ERRORS`` and escaped the MCP tool as a stack trace.
* #542 — forking from ``origin/<base>`` (#454) makes the auto-stash pop collide
  whenever a local *untracked* file shares a path tracked on the base branch.
* #478 — attaching to an already-RUNNING session rolled the freshly created
  branch straight back, so development continued on the shared branch.

The real-git repository fixture mirrors ``test_start_task_remote_base``: the
stash-pop collision only reproduces against genuine git, since it depends on
git's own untracked-restore semantics rather than on the argv we emit.
"""

import asyncio
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from odoo_sdk.mcp.server import _BOUNDARY_ERRORS, _error_boundary
from odoo_sdk.mcp.tools.start_task import (
    _BranchSetupError,
    _create_task_branch,
    make_start_task_tool,
)
from odoo_sdk.state import TaskAlreadyRunningError

# Reuse the shared fakes so these tests stay faithful to the real git contract
# encoded in ``_make_sp`` rather than reinventing a divergent stub.
from tests.test_mcp.test_tools import _accepted, _confirmed, _make_sp

_SP_PATCH = "odoo_sdk.mcp.tools.start_task.subprocess"


def _run(coro):
    return asyncio.run(coro)


def _calls(sp):
    return [c.args[0] for c in sp.run.call_args_list]


def _failing_sp(failing_prefix, **kwargs):
    """Wrap :func:`_make_sp` so calls starting with ``failing_prefix`` raise.

    Models git aborting a specific step (``check=True`` turning a non-zero exit
    into ``CalledProcessError``) while every other command behaves normally, so
    a test can place the failure precisely where the issue reports it.
    """
    sp = _make_sp(**kwargs)
    inner = sp.run.side_effect

    def _maybe_fail(args, **kw):
        if args[: len(failing_prefix)] == failing_prefix:
            raise subprocess.CalledProcessError(1, args)
        return inner(args, **kw)

    sp.run.side_effect = _maybe_fail
    return sp


def _never_pops_sp(**kwargs):
    """Wrap :func:`_make_sp` so every ``git stash pop`` reports failure.

    Models the worst case of #542: even the recovery pop on the original branch
    cannot apply, so the user's work stays in ``stash@{0}`` and the message must
    say so instead of claiming a restore that did not happen.
    """
    sp = _make_sp(**kwargs)
    inner = sp.run.side_effect

    def _fail_pops(args, **kw):
        result = inner(args, **kw)
        if args[1:3] == ["stash", "pop"]:
            result.returncode = 1
        return result

    sp.run.side_effect = _fail_pops
    return sp


class _Reg:
    """Registry whose ``start_task`` command raises ``error`` (or succeeds)."""

    def __init__(self, error=None):
        self._error = error
        self.client = MagicMock()
        self.client.execute.return_value = [
            {"id": 10, "name": "Fix", "project_id": [5, "Acct"]}
        ]

    def __getitem__(self, name):
        cmd = MagicMock()
        cmd._client = self.client
        if name == "start_task" and self._error is not None:
            cmd.execute.side_effect = self._error
        else:
            cmd.execute.side_effect = lambda *a, **k: {"run_id": 1, **k}
        return cmd


def _ctx():
    """A ctx that confirms the start, then picks base branch #1."""
    ctx = MagicMock()
    ctx.elicit = AsyncMock(side_effect=[_confirmed(), _accepted(MagicMock(selection=1))])
    ctx.sample = AsyncMock(return_value=MagicMock(text="fix"))
    return ctx


class TestGitFailuresAreCallerActionable(unittest.TestCase):
    """#541: git's ``CalledProcessError`` reaches callers as a payload."""

    def test_called_process_error_is_a_boundary_error(self):
        self.assertIn(subprocess.CalledProcessError, _BOUNDARY_ERRORS)

    def test_boundary_renders_git_failure_instead_of_raising(self):
        # Before the fix this escaped ``_error_boundary`` as a raw traceback.
        def _tool():
            raise subprocess.CalledProcessError(128, ["git", "checkout", "-b", "10#fix"])

        payload = _error_boundary(_tool)()
        self.assertEqual(payload["error"]["type"], "CalledProcessError")
        self.assertIn("128", payload["error"]["message"])


class TestSetupFailureLeavesNoDanglingBranch(unittest.TestCase):
    """#541: branch setup runs inside the rollback scope."""

    def test_failed_checkout_restores_the_stashed_work(self):
        # The checkout aborts while the tree is stashed. The helper must put the
        # work back before re-raising, rather than leaving it parked in a stash
        # the user never asked for.
        sp = _failing_sp(["git", "checkout", "-b"], dirty=True)
        with patch(_SP_PATCH, sp):
            with self.assertRaises(subprocess.CalledProcessError):
                _create_task_branch("10#fix", "main")
        self.assertIn(["git", "stash", "pop"], _calls(sp))

    def test_setup_failure_propagates_typed_through_the_tool(self):
        # The tool must not swallow it into an ``{"error": ...}`` dict: the MCP
        # boundary owns the rendering (see the boundary test above).
        sp = _failing_sp(["git", "checkout", "-b"], dirty=True)
        tool = make_start_task_tool(_Reg())
        with patch(_SP_PATCH, sp):
            with self.assertRaises(subprocess.CalledProcessError):
                _run(tool("Fix", _ctx(), task_id=10))
        # No branch was ever created, so nothing may be force-deleted either.
        self.assertNotIn("-D", [arg for call in _calls(sp) for arg in call])

    def test_command_failure_still_rolls_back_a_created_branch(self):
        # Regression guard for the new ``except TaskAlreadyRunningError`` clause
        # sitting ahead of the generic one: every *other* failure must keep the
        # #164 rollback behaviour.
        sp = _make_sp()
        tool = make_start_task_tool(_Reg(error=RuntimeError("odoo exploded")))
        with patch(_SP_PATCH, sp):
            with self.assertRaises(RuntimeError):
                _run(tool("Fix", _ctx(), task_id=10))
        self.assertIn(["git", "branch", "-D", "10#fix"], _calls(sp))


class TestBranchSurvivesAttachToRunningSession(unittest.TestCase):
    """#478: attaching to a RUNNING session keeps the new task branch."""

    def test_running_session_does_not_roll_the_branch_back(self):
        sp = _make_sp()
        running = TaskAlreadyRunningError("Task 'Fix' already has an active session")
        tool = make_start_task_tool(_Reg(error=running))
        with patch(_SP_PATCH, sp):
            with self.assertRaises(TaskAlreadyRunningError):
                _run(tool("Fix", _ctx(), task_id=10))
        calls = _calls(sp)
        # The branch was created and checked out ...
        self.assertTrue(
            any(c[:3] == ["git", "checkout", "-b"] and c[3] == "10#fix" for c in calls),
            "the task branch must still be created when a session is running",
        )
        # ... and, unlike every other failure, left in place.
        self.assertNotIn(["git", "branch", "-D", "10#fix"], calls)
        self.assertNotIn(["git", "checkout", "main"], calls)


class TestUnrecoverablePopKeepsTheStash(unittest.TestCase):
    """#542: when even the recovery pop fails, say where the work actually is."""

    def test_message_points_at_the_retained_stash_entry(self):
        sp = _never_pops_sp(dirty=True)
        with patch(_SP_PATCH, sp):
            with self.assertRaises(_BranchSetupError) as caught:
                _create_task_branch("10#fix", "main")
        self.assertIn("stash@{0}", str(caught.exception))
        # The branch is still torn down and the original checked out — the stash
        # entry is the only thing left for the user to deal with.
        calls = _calls(sp)
        self.assertIn(["git", "checkout", "--force", "main"], calls)
        self.assertIn(["git", "branch", "-D", "10#fix"], calls)


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


class TestStashPopCollisionIsUnwound(unittest.TestCase):
    """#542: an untracked/tracked path collision no longer strands the user.

    Reproduction from the issue: the user has an untracked ``feature_module.py``
    locally while ``origin/main`` has just gained a *tracked* file at that exact
    path. ``stash push -u`` parks the untracked copy, the fork from
    ``origin/main`` materialises origin's version, and ``stash pop`` then refuses
    to overwrite it. Pre-fix the run aborted right there: still on the task
    branch, origin's content on disk, the user's copy reachable only via
    ``stash@{0}``.
    """

    def setUp(self):
        self._prev_cwd = Path.cwd()
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)

        self.origin = root / "origin.git"
        _git(root, "init", "--bare", "-b", "main", str(self.origin))

        self.local = root / "local"
        _git(root, "clone", str(self.origin), str(self.local))
        (self.local / "base.txt").write_text("base\n")
        _git(self.local, "add", "base.txt")
        _git(self.local, "commit", "-m", "initial")
        _git(self.local, "push", "origin", "main")
        self.local_tip = _git(self.local, "rev-parse", "main")

        # Someone else lands ``feature_module.py`` on origin/main; the local
        # clone never pulls, so locally that path is still free.
        advancer = root / "advancer"
        _git(root, "clone", str(self.origin), str(advancer))
        (advancer / "feature_module.py").write_text("# theirs\n")
        _git(advancer, "add", "feature_module.py")
        _git(advancer, "commit", "-m", "add feature module")
        _git(advancer, "push", "origin", "main")

        # The user's own untracked work at the colliding path.
        (self.local / "feature_module.py").write_text("# mine, hours of work\n")
        os.chdir(self.local)

    def tearDown(self):
        os.chdir(self._prev_cwd)
        self._tmp.cleanup()

    def test_collision_reports_an_actionable_error(self):
        with self.assertRaises(_BranchSetupError) as caught:
            _create_task_branch("10#fix", "main")
        self.assertIn("10#fix", str(caught.exception))

    def test_user_work_is_back_on_disk_on_the_original_branch(self):
        with self.assertRaises(_BranchSetupError):
            _create_task_branch("10#fix", "main")

        self.assertEqual(_git(self.local, "rev-parse", "--abbrev-ref", "HEAD"), "main")
        self.assertEqual(_git(self.local, "rev-parse", "HEAD"), self.local_tip)
        self.assertEqual(
            (self.local / "feature_module.py").read_text(),
            "# mine, hours of work\n",
            "the user's untracked copy must win, not origin's tracked one",
        )

    def test_no_half_created_branch_is_left_behind(self):
        with self.assertRaises(_BranchSetupError):
            _create_task_branch("10#fix", "main")
        self.assertNotIn(
            "10#fix", _git(self.local, "branch", "--format=%(refname:short)").split()
        )

    def test_nothing_is_left_parked_in_the_stash(self):
        with self.assertRaises(_BranchSetupError):
            _create_task_branch("10#fix", "main")
        self.assertEqual(
            _git(self.local, "stash", "list"),
            "",
            "the auto-stash must be re-applied, not abandoned",
        )

    def test_tool_flow_surfaces_the_collision_as_an_error_payload(self):
        # End-to-end: the flow returns the actionable message instead of raising
        # a git traceback, and never reaches the start command.
        reg = _Reg()
        tool = make_start_task_tool(reg)
        result = _run(tool("Fix", _ctx(), task_id=10))
        self.assertIn("error", result)
        self.assertIn("10#fix", result["error"])
        self.assertEqual(_git(self.local, "rev-parse", "--abbrev-ref", "HEAD"), "main")


if __name__ == "__main__":
    unittest.main()
