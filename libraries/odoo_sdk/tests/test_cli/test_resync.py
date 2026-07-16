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
        with patch(f"{_MOD}.TaskStateDB", return_value=MagicMock()), patch(
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

    def test_google_sources_opt_in_only(self):
        # gcal/gmail are never reached by the default source string.
        git = MagicMock(return_value={"inserted": 0})
        cal = MagicMock()
        out = self._run(
            ["resync"],
            sync_git_log=git,
            sync_github=MagicMock(return_value={"inserted": 0}),
            sync_odoo_chatter=MagicMock(return_value={"inserted": 0}),
            assert_odoo_devcontainer=MagicMock(),
            OdooClient=MagicMock(),
            sync_google_calendar=cal,
        )
        cal.assert_not_called()
        self.assertNotIn("gcal", out)

    def test_gcal_runs_when_requested(self):
        cal = MagicMock(return_value={"inserted": 13})
        out = self._run(
            ["resync", "--sources", "gcal"],
            sync_google_calendar=cal,
            LocalConfig=MagicMock(),
        )
        cal.assert_called_once()
        self.assertEqual(out.strip(), "gcal: inserted 13")

    def test_google_auth_error_surfaces_as_skip_line(self):
        # A missing/expired credential raises; the CLI shows the actionable
        # message as this source's skip reason instead of aborting the resync.
        from odoo_sdk.adapters import GoogleAuthError

        cal = MagicMock(side_effect=GoogleAuthError("no token at /x; re-run helper"))
        out = self._run(
            ["resync", "--sources", "gcal"],
            sync_google_calendar=cal,
            LocalConfig=MagicMock(),
        )
        self.assertIn("gcal: skipped (no token at /x; re-run helper)", out)

    def test_google_api_error_surfaces_as_skip_line(self):
        # A transient REST failure must not abort the whole resync either.
        from odoo_sdk.adapters import GoogleAPIError

        mail = MagicMock(side_effect=GoogleAPIError("GET ... failed: timeout"))
        out = self._run(
            ["resync", "--sources", "gmail"],
            sync_gmail=mail,
            LocalConfig=MagicMock(),
        )
        self.assertIn("gmail: skipped (GET ... failed: timeout)", out)


def _apply(patches):
    """Context manager applying a dict of ``name -> mock`` patches on the CLI."""
    from contextlib import ExitStack

    stack = ExitStack()
    for name, mock in patches.items():
        stack.enter_context(patch(f"{_MOD}.{name}", mock))
    return stack


if __name__ == "__main__":
    unittest.main()
