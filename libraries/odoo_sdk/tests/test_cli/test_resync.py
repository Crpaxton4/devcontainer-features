"""Smoke tests for the ``odoo-sdk resync`` subcommand (issue #328).

The pullers themselves are unit-tested against faked tools elsewhere; here they
are patched so the tests assert only the CLI's wiring: source selection, the
lazy env-guarded Odoo path, and the per-source output lines.
"""

import unittest
from io import StringIO
from unittest.mock import MagicMock, patch

import odoo_sdk.cli.__main__ as cli
from odoo_sdk.utilities.env import OdooDevcontainerRequiredError

_MOD = "odoo_sdk.cli.__main__"


class TestCmdResync(unittest.TestCase):
    def _run(self, argv, **patches):
        out = StringIO()
        with patch(f"{_MOD}._open_local_db", return_value=MagicMock()), patch(
            "sys.stdout", out
        ), patch("sys.argv", ["odoo-sdk", *argv]):
            with _apply(patches):
                cli.main()
        return out.getvalue()

    def test_default_runs_all_sources(self):
        out = self._run(
            ["resync"],
            sync_git_log=MagicMock(return_value={"inserted": 2}),
            sync_github=MagicMock(return_value={"inserted": 1}),
            sync_odoo_chatter=MagicMock(return_value={"inserted": 3}),
            assert_odoo_devcontainer=MagicMock(),
            OdooClient=MagicMock(),
        )
        self.assertIn("git: inserted 2", out)
        self.assertIn("github: inserted 1", out)
        self.assertIn("odoo: inserted 3", out)

    def test_subset_runs_only_requested(self):
        git = MagicMock(return_value={"inserted": 0})
        gh = MagicMock()
        out = self._run(["resync", "--sources", "git"], sync_git_log=git, sync_github=gh)
        self.assertEqual(out.strip(), "git: inserted 0")
        git.assert_called_once()
        gh.assert_not_called()

    def test_odoo_skipped_when_env_assert_fails(self):
        odoo = MagicMock()
        out = self._run(
            ["resync", "--sources", "odoo"],
            sync_odoo_chatter=odoo,
            assert_odoo_devcontainer=MagicMock(
                side_effect=OdooDevcontainerRequiredError("nope")
            ),
        )
        # The odoo puller is never built/run, and the command does not crash.
        odoo.assert_not_called()
        self.assertIn("odoo: skipped (odoo devcontainer not configured)", out)

    def test_skip_reason_line_formatting(self):
        out = self._run(
            ["resync", "--sources", "github"],
            sync_github=MagicMock(return_value={"skipped": "gh unavailable"}),
        )
        self.assertEqual(out.strip(), "github: skipped (gh unavailable)")

    def test_resync_is_local_only(self):
        # resync must skip the global Odoo env assert so git/github work outside a
        # devcontainer; the guard lives in the odoo path only.
        self.assertIn("resync", cli._LOCAL_ONLY)


def _apply(patches):
    """Context manager applying a dict of ``name -> mock`` patches on the CLI."""
    from contextlib import ExitStack

    stack = ExitStack()
    for name, mock in patches.items():
        stack.enter_context(patch(f"{_MOD}.{name}", mock))
    return stack


if __name__ == "__main__":
    unittest.main()
