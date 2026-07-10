"""Regression tests for #163: an untracked-only tree's auto-stash pops balanced.

Background: ``_create_task_branch`` auto-stashes a dirty working tree before
switching branches and pops afterwards. A plain ``git stash push`` saves nothing
on an untracked-only tree, so an unconditional ``git stash pop`` would abort with
"No stash entries found". PR #150 fixed this by stashing with ``push -u`` (which
carries untracked files, creating a real entry) and popping only when an entry
was actually pushed.

These tests are *complementary* to
``test_tools.test_untracked_only_tree_does_not_pop_without_entry``: that test
asserts the presence of ``push -u`` / ``pop`` in the command list, whereas these
assert the stash accounting is **balanced** (net-zero, no dangling entry, pop
never hits an empty stash) and verify the balance through the full ``start_task``
tool flow — a different assertion angle, not a copy.
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from odoo_sdk.mcp.tools.start_task import make_start_task_tool

# Reuse the shared fakes so this file stays faithful to the real git contract
# encoded in ``_make_sp`` rather than reinventing a divergent stub.
from tests.test_mcp.test_tools import _accepted, _confirmed, _make_sp

_SP_PATCH = "odoo_sdk.mcp.tools.start_task.subprocess"


def _run(coro):
    return asyncio.run(coro)


def _calls(sp):
    return [c.args[0] for c in sp.run.call_args_list]


def _recording_sp(**kwargs):
    """Wrap :func:`_make_sp` so each git call's ``(argv, returncode)`` is logged.

    The shared ``_make_sp`` fake mutates a private ``stash_entries`` counter as
    it runs, so re-invoking its side_effect after the fact would corrupt that
    state and yield bogus return codes. Instead we record the *actual* result of
    every call as the flow makes it, giving us a faithful, non-destructive view
    of what each ``git stash pop`` really returned.
    """
    sp = _make_sp(**kwargs)
    inner = sp.run.side_effect
    log: list[tuple[list, int]] = []

    def _record(args, **kw):
        result = inner(args, **kw)
        log.append((args, result.returncode))
        return result

    sp.run.side_effect = _record
    sp.recorded = log
    return sp


def _returncodes_for(sp, prefix):
    """Return recorded return codes for calls whose argv starts with ``prefix``."""
    return [rc for argv, rc in sp.recorded if argv[: len(prefix)] == prefix]


class TestUntrackedStashPopIsBalanced(unittest.TestCase):
    """#163: the untracked-only auto-stash/pop cycle is net-balanced."""

    def test_stash_accounting_returns_to_zero(self):
        # A fresh fake starts with zero stash entries. After the whole branch
        # setup the stash must be empty again: the ``push -u`` entry created for
        # the untracked file has been popped, leaving nothing dangling. We derive
        # the net balance from the recorded calls: every successful push (rc 0)
        # added an entry, every successful pop removed one, and they must cancel.
        from odoo_sdk.mcp.tools.start_task import _create_task_branch

        sp = _recording_sp(dirty=True, dirty_kind="untracked")
        with patch(_SP_PATCH, sp):
            _create_task_branch("10#fix", "main")  # must not raise
        net = sum(
            1 for argv, rc in sp.recorded if argv[1:3] == ["stash", "push"] and rc == 0
        ) - sum(
            1 for argv, rc in sp.recorded if argv[1:3] == ["stash", "pop"] and rc == 0
        )
        self.assertEqual(
            net, 0, "stash must be empty after a balanced push -u / pop cycle"
        )

    def test_exactly_one_push_and_one_pop(self):
        # Balance means symmetry: precisely one stash created and precisely one
        # popped. A stray extra push (leak) or extra pop (double-pop) would both
        # be regressions the pairing count catches.
        from odoo_sdk.mcp.tools.start_task import _create_task_branch

        sp = _make_sp(dirty=True, dirty_kind="untracked")
        with patch(_SP_PATCH, sp):
            _create_task_branch("10#fix", "main")
        calls = _calls(sp)
        pushes = [c for c in calls if c[1:3] == ["stash", "push"]]
        pops = [c for c in calls if c[1:3] == ["stash", "pop"]]
        self.assertEqual(len(pushes), 1, "exactly one auto-stash push expected")
        self.assertEqual(len(pops), 1, "exactly one balancing pop expected")
        # And that lone push must carry untracked files, else it would save
        # nothing and the pop below would have nothing to consume.
        self.assertIn("-u", pushes[0])

    def test_pop_finds_an_entry_and_does_not_error(self):
        # The core #163 failure mode was ``git stash pop`` running against an
        # empty stash (returncode 1 -> "No stash entries found", which with
        # ``check=True`` raises CalledProcessError). Assert every pop the flow
        # issues actually finds an entry (returncode 0), proving the push -u
        # populated the stash the pop consumes.
        from odoo_sdk.mcp.tools.start_task import _create_task_branch

        sp = _recording_sp(dirty=True, dirty_kind="untracked")
        with patch(_SP_PATCH, sp):
            _create_task_branch("10#fix", "main")
        pop_codes = _returncodes_for(sp, ["git", "stash", "pop"])
        self.assertTrue(pop_codes, "a balancing pop must run")
        self.assertTrue(
            all(rc == 0 for rc in pop_codes),
            "pop must never hit an empty stash (the #163 regression)",
        )

    def test_tool_flow_pops_balanced_on_untracked_tree(self):
        # End-to-end through the ``start_task`` tool (not just the helper): a
        # dirty untracked tree drives the confirm + branch-pick elicitations and
        # must still finish with a balanced, error-free pop and reach start_task.
        client = MagicMock()
        client.execute.return_value = [
            {"id": 10, "name": "Fix", "project_id": [5, "Acct"]}
        ]

        class _Reg:
            def __getitem__(self, name):
                cmd = MagicMock()
                cmd._client = client
                cmd.execute.side_effect = lambda *a, **k: {"session_id": 1, **k}
                return cmd

        ctx = MagicMock()
        ctx.elicit = AsyncMock(
            side_effect=[_confirmed(), _accepted(MagicMock(selection=1))]
        )
        ctx.sample = AsyncMock(return_value=MagicMock(text="fix"))
        sp = _recording_sp(dirty=True, dirty_kind="untracked")
        tool = make_start_task_tool(_Reg())
        with patch(_SP_PATCH, sp):
            result = _run(tool("Fix", ctx, task_id=10))
        self.assertEqual(result["session_id"], 1)
        pop_codes = _returncodes_for(sp, ["git", "stash", "pop"])
        self.assertTrue(pop_codes and all(rc == 0 for rc in pop_codes))
        # And the tree is clean again: successful pushes and pops net to zero.
        net = sum(
            1 for argv, rc in sp.recorded if argv[1:3] == ["stash", "push"] and rc == 0
        ) - sum(
            1 for argv, rc in sp.recorded if argv[1:3] == ["stash", "pop"] and rc == 0
        )
        self.assertEqual(net, 0)


if __name__ == "__main__":
    unittest.main()
