"""Tests for ``odoo-sdk log-event --repo`` — caller-stated repo attribution (#742).

The repo an event belongs to is the repo of the *session's* cwd, which is not
the cwd the CLI process happens to run in: the hook shim is spawned by Claude
Code from wherever it likes, so leaving the SDK to derive the label from its own
working directory attributed hook events to the wrong repo (or to none at all).
``--repo`` lets the caller state it, and the SDK — not the shell shim — owns the
normalization, so the shim may hand over the raw ``git remote get-url origin``
output. Whatever shape arrives, it collapses to the same ``owner/repo`` label a
cwd-derived one would, which is what keeps a hook event and the commit that
accompanies it on one repo lane rather than two spellings of it.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from io import StringIO
from unittest.mock import patch

import odoo_sdk.cli.__main__ as cli
from odoo_sdk.commands.log_event import normalize_repo_label
from odoo_sdk.state import LocalStateClient as TaskStateDB
from odoo_sdk.state.db import tracker_db_path
from tests.support import provision_schema


def _run(argv: list[str]) -> None:
    """Invoke ``cli.main`` with ``argv``, swallowing its stdio."""
    with (
        patch("sys.argv", ["odoo-sdk", *argv]),
        patch("sys.stderr", StringIO()),
        patch("sys.stdout", StringIO()),
    ):
        cli.main()


class TestNormalizeRepoLabel(unittest.TestCase):
    """The core-layer door itself, exercised without the CLI or a state DB."""

    def test_ssh_url_collapses_to_owner_repo(self) -> None:
        self.assertEqual(
            normalize_repo_label("git@github.com:owner/repo.git"), "owner/repo"
        )

    def test_https_url_collapses_to_owner_repo(self) -> None:
        self.assertEqual(
            normalize_repo_label("https://github.com/owner/repo.git"), "owner/repo"
        )

    def test_plain_label_passes_through(self) -> None:
        self.assertEqual(normalize_repo_label("owner/repo"), "owner/repo")

    def test_single_segment_label_kept_verbatim(self) -> None:
        # The shim's no-remote fallback: `basename $(git rev-parse --show-toplevel)`.
        self.assertEqual(normalize_repo_label("my-checkout"), "my-checkout")

    def test_surrounding_whitespace_is_stripped(self) -> None:
        self.assertEqual(normalize_repo_label("  owner/repo\n"), "owner/repo")

    def test_none_defers_to_cwd_derivation(self) -> None:
        # ``None`` is LogEventCommand.execute's "resolve from the working tree"
        # sentinel; it must NOT normalize to a literal label.
        self.assertIsNone(normalize_repo_label(None))

    def test_blank_defers_to_cwd_derivation(self) -> None:
        # A blank would otherwise derive to the "(unknown)" fallback label and
        # permanently mis-attribute the row.
        self.assertIsNone(normalize_repo_label(""))
        self.assertIsNone(normalize_repo_label("   "))


class TestLogEventRepoFlag(unittest.TestCase):
    """``--repo`` end to end: argv in, ``events.repo`` column out."""

    def setUp(self) -> None:
        self._cwd = os.getcwd()
        self._state = tempfile.mkdtemp()
        self._repo = tempfile.mkdtemp()
        subprocess.run(["git", "init"], cwd=self._repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "remote", "add", "origin", "https://example.com/cwd/checkout.git"],
            cwd=self._repo,
            check=True,
            capture_output=True,
        )
        os.environ["ODOO_TASK_TRACKER_DIR"] = self._state
        # The central DB is host-provisioned; the SDK never creates it.
        provision_schema(tracker_db_path(self._state))
        os.chdir(self._repo)

    def tearDown(self) -> None:
        os.chdir(self._cwd)
        os.environ.pop("ODOO_TASK_TRACKER_DIR", None)
        shutil.rmtree(self._state, ignore_errors=True)
        shutil.rmtree(self._repo, ignore_errors=True)

    def _logged_repo(self, *extra: str) -> str:
        _run(["log-event", "--source", "claude:PreToolUse", *extra])
        events = TaskStateDB().get_events()
        self.assertEqual(len(events), 1)
        return events[0].repo

    def test_ssh_url_is_normalized(self) -> None:
        self.assertEqual(
            self._logged_repo("--repo", "git@github.com:acme/widgets.git"),
            "acme/widgets",
        )

    def test_https_url_is_normalized(self) -> None:
        self.assertEqual(
            self._logged_repo("--repo", "https://github.com/acme/widgets.git"),
            "acme/widgets",
        )

    def test_plain_label_passes_through(self) -> None:
        self.assertEqual(self._logged_repo("--repo", "acme/widgets"), "acme/widgets")

    def test_stated_repo_wins_over_the_cwd_remote(self) -> None:
        # The whole point of the flag: the cwd IS a git repo with its own origin,
        # and the stated repo must still be what lands.
        self.assertEqual(self._logged_repo("--repo", "acme/widgets"), "acme/widgets")

    def test_absent_repo_falls_back_to_cwd_derivation(self) -> None:
        self.assertEqual(self._logged_repo(), "cwd/checkout")

    def test_blank_repo_falls_back_to_cwd_derivation(self) -> None:
        # Defensive: an explicitly blank value from any caller must behave like
        # an absent one rather than recording the "(unknown)" fallback label.
        self.assertEqual(self._logged_repo("--repo", ""), "cwd/checkout")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
