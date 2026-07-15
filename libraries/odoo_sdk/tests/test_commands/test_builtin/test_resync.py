"""Tests for the ``resync`` builtin command orchestration (issue #328).

The individual pullers are exercised in ``tests/test_adapters/test_external_sync``;
here the pullers are patched so the tests assert only the command's own behavior:
source selection, which pullers run, and how the client/state are threaded.
"""

import unittest
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
        git.assert_called_once_with(state, cmd.config, client)
        gh.assert_called_once_with(state, cmd.config, client)
        odoo.assert_called_once_with(client, state, cmd.config)

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

    def test_registered_metadata(self) -> None:
        self.assertEqual(ResyncCommand._name, "resync")
        self.assertIn("Reconcile", ResyncCommand._description)


if __name__ == "__main__":
    unittest.main()
