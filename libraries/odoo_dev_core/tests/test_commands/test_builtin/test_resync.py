"""Tests for the ``resync`` builtin: a thin handler over the resync workflow.

The workflow's dispatch is covered in ``tests/test_workflows/test_resync.py`` and
the pullers in ``libraries/odoo_dev_data/tests/test_adapters_sync.py``. Here the
tests assert only the handler: the string and date arguments it parses, and that
it returns the workflow's per-source results.
"""

import unittest
from datetime import date
from unittest.mock import MagicMock

from ...fakes import FakeChatter, FakeGit, FakeGitHub, service
from odoo_dev_core import default_registry


class TestResyncCommand(unittest.TestCase):
    def _command(self, git=None, github=None, chatter=None):
        client = MagicMock(name="client")
        state = MagicMock(name="state")
        config = MagicMock(name="config")
        git = git or FakeGit({"inserted": 2})
        github = github or FakeGitHub({"inserted": 1})
        chatter = chatter or FakeChatter({"inserted": 3})
        cmd = service(
            client=client,
            events=state,
            config=config,
            git=git,
            github=github,
            chatter=chatter,
        )
        return cmd, client, state, config, (git, github, chatter)

    def test_runs_all_sources_by_default(self) -> None:
        cmd, client, state, config, (git, gh, odoo) = self._command()
        result = cmd.execute("resync")
        self.assertEqual(
            result,
            {
                "git": {"inserted": 2},
                "github": {"inserted": 1},
                "odoo": {"inserted": 3},
            },
        )
        # git/github receive config (window/authors) and the client (task-id
        # validation); the odoo puller is client-first, then state and config.
        # Without an explicit range every windowed puller sees start=end=None.
        window = {"start": None, "end": None}
        self.assertEqual(
            git.calls, [{"args": (state, config, client), "kwargs": window}]
        )
        self.assertEqual(
            gh.calls, [{"args": (state, config, client), "kwargs": window}]
        )
        self.assertEqual(
            odoo.calls, [{"args": (client, state, config), "kwargs": window}]
        )

    def test_subset_runs_only_requested_pullers(self) -> None:
        cmd, _client, _state, _config, (git, gh, odoo) = self._command()
        result = cmd.execute("resync", sources="git")
        self.assertEqual(result, {"git": {"inserted": 2}})
        self.assertEqual(len(git.calls), 1)
        self.assertEqual(gh.calls, [])
        self.assertEqual(odoo.calls, [])

    def test_skip_reasons_pass_through(self) -> None:
        cmd, *_ = self._command(
            git=FakeGit({"skipped": "no git"}),
            github=FakeGitHub({"skipped": "no gh"}),
            chatter=FakeChatter({"skipped": "no odoo"}),
        )
        result = cmd.execute("resync", sources="git,github,odoo")
        self.assertEqual(
            result,
            {
                "git": {"skipped": "no git"},
                "github": {"skipped": "no gh"},
                "odoo": {"skipped": "no odoo"},
            },
        )

    def test_start_end_thread_into_the_pullers(self) -> None:
        # git/github/odoo receive the parsed dates (#652).
        cmd, _client, _state, _config, (git, gh, odoo) = self._command()
        cmd.execute(
            "resync",
            sources="git,github,odoo",
            start="2026-07-01",
            end="2026-07-31",
        )
        expected = {"start": date(2026, 7, 1), "end": date(2026, 7, 31)}
        for gateway in (git, gh, odoo):
            self.assertEqual(gateway.calls[0]["kwargs"], expected)

    def test_invalid_iso_date_raises_value_error(self) -> None:
        cmd, _client, _state, _config, (git, _gh, _odoo) = self._command()
        with self.assertRaises(ValueError):
            cmd.execute("resync", sources="git", start="not-a-date")
        # The range fails loudly BEFORE any puller runs.
        self.assertEqual(git.calls, [])

    def test_error_results_pass_through(self) -> None:
        cmd, *_ = self._command(
            git=FakeGit({"error": "no repos"}),
            github=FakeGitHub({"inserted": 1, "found": 1}),
        )
        result = cmd.execute("resync", sources="git,github")
        self.assertEqual(result["git"], {"error": "no repos"})
        self.assertEqual(result["github"], {"inserted": 1, "found": 1})

    def test_registered_metadata(self) -> None:
        spec = default_registry().get("resync")
        self.assertEqual(spec.id, "resync")
        self.assertIn("Reconcile", spec.description)
        # #652: the command is no longer described as current-repo-scoped.
        self.assertNotIn("current repo", spec.description)


if __name__ == "__main__":
    unittest.main()
