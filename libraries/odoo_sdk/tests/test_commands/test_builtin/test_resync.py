"""Tests for the ``resync`` builtin command orchestration (issues #328, #652).

The individual pullers are exercised in ``tests/test_adapters/test_external_sync``;
here the pullers are patched so the tests assert only the command's own behavior:
source selection, which pullers run, how the client/state are threaded, and how
the explicit ``start``/``end`` range reaches (only) the git/github/odoo pullers.
"""

import unittest
from datetime import date
from unittest.mock import MagicMock, patch

from odoo_sdk.commands.builtin import ResyncCommand
from odoo_sdk.commands.builtin.resync import _parse_sources

_MOD = "odoo_sdk.commands.builtin.resync"


class TestParseSources(unittest.TestCase):
    def test_default_and_blank_select_all(self) -> None:
        self.assertEqual(_parse_sources("git,github,odoo"), ["git", "github", "odoo"])
        self.assertEqual(_parse_sources(""), ["git", "github", "odoo"])
        self.assertEqual(_parse_sources("  "), ["git", "github", "odoo"])

    def test_subset_kept_in_stable_order(self) -> None:
        # Order follows the canonical order, not the input order.
        self.assertEqual(_parse_sources("odoo,git"), ["git", "odoo"])

    def test_unknown_tokens_ignored(self) -> None:
        self.assertEqual(_parse_sources("git,bogus"), ["git"])


class TestResyncCommand(unittest.TestCase):
    def _command(self):
        client = MagicMock(name="client")
        state = MagicMock(name="state")
        cmd = ResyncCommand(client, state=state, config=MagicMock())
        return cmd, client, state

    def test_runs_all_sources_by_default(self) -> None:
        cmd, client, state = self._command()
        with patch(f"{_MOD}.sync_git_log", return_value={"inserted": 2}) as git, patch(
            f"{_MOD}.sync_github", return_value={"inserted": 1}
        ) as gh, patch(
            f"{_MOD}.sync_odoo_chatter", return_value={"inserted": 3}
        ) as odoo:
            result = cmd.execute()
        self.assertEqual(
            result,
            {
                "git": {"inserted": 2},
                "github": {"inserted": 1},
                "odoo": {"inserted": 3},
            },
        )
        # git/github now receive config (window/authors) and the client (task-id
        # validation); the odoo puller keeps client-first, then state and config.
        # Without an explicit range every windowed puller sees start=end=None.
        git.assert_called_once_with(state, cmd.config, client, start=None, end=None)
        gh.assert_called_once_with(state, cmd.config, client, start=None, end=None)
        odoo.assert_called_once_with(client, state, cmd.config, start=None, end=None)

    def test_subset_runs_only_requested_pullers(self) -> None:
        cmd, _client, _state = self._command()
        with patch(f"{_MOD}.sync_git_log", return_value={"inserted": 0}) as git, patch(
            f"{_MOD}.sync_github"
        ) as gh, patch(f"{_MOD}.sync_odoo_chatter") as odoo:
            result = cmd.execute(sources="git")
        self.assertEqual(result, {"git": {"inserted": 0}})
        git.assert_called_once()
        gh.assert_not_called()
        odoo.assert_not_called()

    def test_skip_reasons_pass_through(self) -> None:
        cmd, _client, _state = self._command()
        with patch(f"{_MOD}.sync_git_log", return_value={"skipped": "no git"}), patch(
            f"{_MOD}.sync_github", return_value={"skipped": "no gh"}
        ), patch(f"{_MOD}.sync_odoo_chatter", return_value={"skipped": "no odoo"}):
            result = cmd.execute(sources="git,github,odoo")
        self.assertEqual(
            result,
            {
                "git": {"skipped": "no git"},
                "github": {"skipped": "no gh"},
                "odoo": {"skipped": "no odoo"},
            },
        )

    def test_start_end_thread_into_windowed_pullers_only(self) -> None:
        # git/github/odoo receive the parsed dates; the Google pullers keep
        # their own google_sync_window_days window and never see them (#652).
        cmd, client, state = self._command()
        with patch(
            f"{_MOD}.sync_git_log", return_value={"inserted": 0, "found": 0, "repos": 1}
        ) as git, patch(
            f"{_MOD}.sync_github", return_value={"inserted": 0, "found": 0}
        ) as gh, patch(
            f"{_MOD}.sync_odoo_chatter", return_value={"inserted": 0}
        ) as odoo, patch(
            f"{_MOD}.sync_google_calendar", return_value={"inserted": 0}
        ) as gcal:
            cmd.execute(
                sources="git,github,odoo,gcal", start="2026-07-01", end="2026-07-31"
            )
        expected = {"start": date(2026, 7, 1), "end": date(2026, 7, 31)}
        git.assert_called_once_with(state, cmd.config, client, **expected)
        gh.assert_called_once_with(state, cmd.config, client, **expected)
        odoo.assert_called_once_with(client, state, cmd.config, **expected)
        gcal.assert_called_once_with(state, cmd.config)

    def test_invalid_iso_date_raises_value_error(self) -> None:
        cmd, _client, _state = self._command()
        with patch(f"{_MOD}.sync_git_log") as git:
            with self.assertRaises(ValueError):
                cmd.execute(sources="git", start="not-a-date")
        # The range fails loudly BEFORE any puller runs.
        git.assert_not_called()

    def test_error_results_pass_through(self) -> None:
        cmd, _client, _state = self._command()
        with patch(
            f"{_MOD}.sync_git_log", return_value={"error": "no repos"}
        ), patch(f"{_MOD}.sync_github", return_value={"inserted": 1, "found": 1}):
            result = cmd.execute(sources="git,github")
        self.assertEqual(result["git"], {"error": "no repos"})
        self.assertEqual(result["github"], {"inserted": 1, "found": 1})

    def test_google_errors_degrade_to_skip(self) -> None:
        # At the shared command surface (TUI/MCP) a Google credentials failure
        # must become a per-source skip — matching the CLI — not an unhandled
        # exception escaping the MCP boundary.
        from odoo_sdk.adapters import GoogleAuthError

        cmd, _client, _state = self._command()
        with patch(
            f"{_MOD}.sync_google_calendar",
            side_effect=GoogleAuthError("no token at /x; re-run helper"),
        ):
            result = cmd.execute(sources="gcal")
        self.assertEqual(
            result, {"gcal": {"skipped": "no token at /x; re-run helper"}}
        )

    def test_google_range_gets_ignored_note(self) -> None:
        cmd, _client, _state = self._command()
        with patch(f"{_MOD}.sync_google_calendar", return_value={"inserted": 3}):
            result = cmd.execute(sources="gcal", start="2026-07-01")
        self.assertEqual(result["gcal"]["inserted"], 3)
        self.assertIn("start/end ignored", result["gcal"]["note"])

    def test_google_without_range_has_no_note(self) -> None:
        cmd, _client, _state = self._command()
        with patch(f"{_MOD}.sync_google_calendar", return_value={"inserted": 3}):
            result = cmd.execute(sources="gcal")
        self.assertEqual(result, {"gcal": {"inserted": 3}})

    def test_registered_metadata(self) -> None:
        self.assertEqual(ResyncCommand._name, "resync")
        self.assertIn("Reconcile", ResyncCommand._description)
        # #652: the command is no longer described as current-repo-scoped.
        self.assertNotIn("current repo", ResyncCommand._description)


if __name__ == "__main__":
    unittest.main()
