"""Tests for the opt-in Google (gcal/gmail) wiring of the resync command (#370).

The pullers themselves are exercised in ``tests/test_adapters/test_google_sync``;
here they are patched so the tests assert only the command's own behavior: that
Google sources are opt-in (never selected by default) and are threaded the
state and config peer dependencies.
"""

import unittest
from unittest.mock import MagicMock, patch

from odoo_sdk.commands.builtin import ResyncCommand
from odoo_sdk.commands.builtin.resync import _parse_sources

_MOD = "odoo_sdk.commands.builtin.resync"


class TestGoogleSourcesOptIn(unittest.TestCase):
    def test_default_excludes_google_sources(self) -> None:
        self.assertEqual(_parse_sources(""), ["git", "github", "odoo"])
        self.assertEqual(_parse_sources("git,github,odoo"), ["git", "github", "odoo"])

    def test_google_sources_only_when_named(self) -> None:
        self.assertEqual(_parse_sources("gcal,gmail"), ["gcal", "gmail"])
        self.assertEqual(_parse_sources("gmail,git"), ["git", "gmail"])

    def test_stable_order_across_all_sources(self) -> None:
        self.assertEqual(
            _parse_sources("gmail,odoo,gcal,git,github"),
            ["git", "github", "odoo", "gcal", "gmail"],
        )


class TestGoogleWiring(unittest.TestCase):
    def _command(self):
        client = MagicMock(name="client")
        state = MagicMock(name="state")
        config = MagicMock(name="config")
        return ResyncCommand(client, state=state, config=config), state, config

    def test_runs_only_google_when_requested(self) -> None:
        cmd, state, config = self._command()
        with patch(f"{_MOD}.sync_google_calendar", return_value={"inserted": 13}) as cal, \
                patch(f"{_MOD}.sync_gmail", return_value={"inserted": 2}) as mail, \
                patch(f"{_MOD}.sync_git_log") as git:
            result = cmd.execute(sources="gcal,gmail")
        self.assertEqual(result, {"gcal": {"inserted": 13}, "gmail": {"inserted": 2}})
        cal.assert_called_once_with(state, config)
        mail.assert_called_once_with(state, config)
        git.assert_not_called()

    def test_google_not_run_by_default(self) -> None:
        cmd, _state, _config = self._command()
        with patch(f"{_MOD}.sync_git_log", return_value={"inserted": 0}), \
                patch(f"{_MOD}.sync_github", return_value={"inserted": 0}), \
                patch(f"{_MOD}.sync_odoo_chatter", return_value={"inserted": 0}), \
                patch(f"{_MOD}.sync_google_calendar") as cal, \
                patch(f"{_MOD}.sync_gmail") as mail:
            result = cmd.execute()
        self.assertNotIn("gcal", result)
        self.assertNotIn("gmail", result)
        cal.assert_not_called()
        mail.assert_not_called()


if __name__ == "__main__":
    unittest.main()
