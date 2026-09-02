"""Smoke tests for the ``odoo-sdk resync`` subcommand (issues #328, #652, #653).

The pullers themselves are unit-tested against faked tools elsewhere; here they
are patched so the tests assert only the CLI's wiring: source selection, the
lazily capability-guarded Odoo path, the explicit ``--start``/``--end`` range,
the per-source output lines, and the error exit contract.
"""

import unittest
from datetime import date
from io import StringIO
from unittest.mock import MagicMock, patch

import odoo_sdk.cli.__main__ as cli
from odoo_sdk.state import TrackerStateMissingError

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

    def _run_expect_exit(self, argv, **patches):
        """Run the CLI expecting ``SystemExit``; return (exit code, stdout)."""
        out = StringIO()
        with patch(f"{_MOD}.TaskStateDB", return_value=MagicMock()), patch(
            "sys.stdout", out
        ), patch("sys.stderr", StringIO()), patch("sys.argv", ["odoo-sdk", *argv]):
            with _apply(patches):
                with self.assertRaises(SystemExit) as ctx:
                    cli.main()
        return ctx.exception.code, out.getvalue()

    def test_default_runs_all_sources(self):
        out = self._run(
            ["resync"],
            sync_git_log=MagicMock(return_value={"inserted": 2}),
            sync_github=MagicMock(return_value={"inserted": 1}),
            sync_odoo_chatter=MagicMock(return_value={"inserted": 3}),
            assert_sdk_configured=MagicMock(),
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

    def test_odoo_skipped_when_tracker_db_missing(self):
        odoo = MagicMock()
        out = self._run(
            ["resync", "--sources", "odoo"],
            sync_odoo_chatter=odoo,
            assert_sdk_configured=MagicMock(
                side_effect=TrackerStateMissingError("no tracker database")
            ),
        )
        # The odoo puller is never built/run, and the command does not crash.
        odoo.assert_not_called()
        self.assertIn("odoo: skipped (odoo sdk not configured)", out)

    def test_odoo_skipped_when_connection_settings_missing(self):
        """The guard's other failure (#642) degrades to the same skip notice."""
        odoo = MagicMock()
        out = self._run(
            ["resync", "--sources", "odoo"],
            sync_odoo_chatter=odoo,
            assert_sdk_configured=MagicMock(
                side_effect=ValueError("Missing Odoo connection settings: url")
            ),
        )
        odoo.assert_not_called()
        self.assertIn("odoo: skipped (odoo sdk not configured)", out)

    def test_skip_reason_line_formatting(self):
        out = self._run(
            ["resync", "--sources", "github"],
            sync_github=MagicMock(return_value={"skipped": "gh unavailable"}),
        )
        self.assertEqual(out.strip(), "github: skipped (gh unavailable)")

    def test_resync_is_local_only(self):
        # resync must skip the global capability assert so git/github work on an
        # unconfigured SDK; the guard lives in the odoo path only.
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
            assert_sdk_configured=MagicMock(),
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

    def test_start_end_thread_date_objects_into_pullers(self):
        git = MagicMock(return_value={"inserted": 0, "found": 0, "repos": 1})
        gh = MagicMock(return_value={"inserted": 0, "found": 0})
        self._run(
            ["resync", "--sources", "git,github", "--start", "2026-07-01",
             "--end", "2026-07-31"],
            sync_git_log=git,
            sync_github=gh,
        )
        self.assertEqual(git.call_args.kwargs["start"], date(2026, 7, 1))
        self.assertEqual(git.call_args.kwargs["end"], date(2026, 7, 31))
        self.assertEqual(gh.call_args.kwargs["start"], date(2026, 7, 1))
        self.assertEqual(gh.call_args.kwargs["end"], date(2026, 7, 31))

    def test_invalid_start_date_is_an_argparse_error(self):
        # argparse rejects garbage dates with its usual exit status 2.
        code, _out = self._run_expect_exit(["resync", "--start", "garbage"])
        self.assertEqual(code, 2)

    def test_coverage_counters_rendered_on_success_lines(self):
        out = self._run(
            ["resync", "--sources", "git,github"],
            sync_git_log=MagicMock(
                return_value={"inserted": 2, "found": 81, "repos": 13}
            ),
            sync_github=MagicMock(return_value={"inserted": 0, "found": 41}),
        )
        self.assertIn("git: inserted 2 (found 81 in 13 repos)", out)
        self.assertIn("github: inserted 0 (found 41)", out)

    def test_failed_repos_counter_rendered(self):
        out = self._run(
            ["resync", "--sources", "git"],
            sync_git_log=MagicMock(
                return_value={"inserted": 1, "found": 1, "repos": 3, "failed_repos": 2}
            ),
        )
        self.assertIn("git: inserted 1 (found 1 in 3 repos; 2 failed)", out)

    def test_unattributed_reviews_warn_on_line_and_stderr(self):
        err = StringIO()
        with patch("sys.stderr", err):
            out = self._run(
                ["resync", "--sources", "github"],
                sync_github=MagicMock(
                    return_value={
                        "inserted": 3,
                        "found": 3,
                        "unattributed_reviews": ["o/r#5", "o/r#9", "acme/x#2"],
                    }
                ),
            )
        self.assertIn(
            "github: inserted 3 (found 3); "
            "WARNING: 3 review event(s) resolved no task id",
            out,
        )
        self.assertIn("unattributed review: o/r#5", err.getvalue())
        self.assertIn("unattributed review: acme/x#2", err.getvalue())

    def test_search_truncation_warning_rendered(self):
        out = self._run(
            ["resync", "--sources", "github"],
            sync_github=MagicMock(
                return_value={
                    "inserted": 5,
                    "found": 200,
                    "warnings": ["authored-PR (octocat) search hit its 200-item cap"],
                }
            ),
        )
        self.assertIn(
            "github: inserted 5 (found 200); "
            "WARNING: authored-PR (octocat) search hit its 200-item cap",
            out,
        )

    def test_google_range_ignored_note_rendered(self):
        # gcal/gmail have no start/end; an explicit range must not be silently
        # discarded — the line says which window actually applied.
        out = self._run(
            ["resync", "--sources", "gcal", "--start", "2026-07-01"],
            sync_google_calendar=MagicMock(return_value={"inserted": 2}),
            LocalConfig=MagicMock(),
        )
        self.assertIn("gcal: inserted 2; note: start/end ignored", out)

    def test_error_result_exits_nonzero_after_all_lines_print(self):
        # An error source fails the command (#652) — but only after every
        # requested source has run and printed its line.
        code, out = self._run_expect_exit(
            ["resync", "--sources", "git,github"],
            sync_git_log=MagicMock(
                return_value={"error": "git unavailable or user.email unset"}
            ),
            sync_github=MagicMock(return_value={"inserted": 1, "found": 1}),
        )
        self.assertEqual(code, 1)
        self.assertIn("git: error (git unavailable or user.email unset)", out)
        self.assertIn("github: inserted 1 (found 1)", out)


def _apply(patches):
    """Context manager applying a dict of ``name -> mock`` patches on the CLI."""
    from contextlib import ExitStack

    stack = ExitStack()
    for name, mock in patches.items():
        stack.enter_context(patch(f"{_MOD}.{name}", mock))
    return stack


if __name__ == "__main__":
    unittest.main()
