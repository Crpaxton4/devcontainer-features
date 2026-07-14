"""Tests for the idempotent resync pullers (issue #328).

Every backing tool is faked: git/gh go through a fake ``subprocess.run`` that
dispatches on the command, and Odoo goes through a structural fake client. No
network, no real git repo, and no live Odoo are involved.
"""

import subprocess
import tempfile
import unittest
from datetime import timezone
from pathlib import Path
from unittest.mock import patch

from odoo_sdk.adapters import external_sync as ex
from odoo_sdk.state import LocalStateClient
from odoo_sdk.transport.errors import OdooError

_SEP = "\x1f"


def _tmp_state() -> LocalStateClient:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return LocalStateClient(db_path=Path(tmp.name))


class _FakeProc:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout


def _fake_run(routes, missing=()):
    """Return a ``subprocess.run`` stand-in dispatching on the command.

    ``routes`` maps a substring matcher (callable ``cmd -> bool``) to a stdout
    string, or to ``None`` to simulate a non-zero exit. Binaries named in
    ``missing`` raise ``FileNotFoundError`` (tool not installed).
    """

    def run(cmd, capture_output=False, text=False, check=False):
        if cmd[0] in missing:
            raise FileNotFoundError(cmd[0])
        for matches, stdout in routes:
            if matches(cmd):
                if stdout is None:
                    raise subprocess.CalledProcessError(1, cmd)
                return _FakeProc(stdout)
        raise subprocess.CalledProcessError(1, cmd)

    return run


def _has(*needles):
    """Match a command by exact argument tokens (avoids substring ambiguity)."""
    return lambda cmd: all(n in cmd for n in needles)


def _last(suffix):
    """Match a command whose final argument ends with ``suffix``."""
    return lambda cmd: cmd[-1].endswith(suffix)


class TestExtractTaskIds(unittest.TestCase):
    """Table test for the single documented task-id extractor."""

    def test_extraction_table(self) -> None:
        cases = [
            ("Fix #123", "", ["123"]),
            ("", "odoo-45", ["45"]),
            ("cleanup [77]", "", ["77"]),
            ("ODOO-9 done", "", ["9"]),  # case-insensitive
            ("#1 and #1 again", "", ["1"]),  # de-duped
            ("#1 then odoo-2 then [3]", "", ["1", "2", "3"]),  # order preserved
            ("no ids here", "main", []),  # nothing extractable
        ]
        for subject, branch, expected in cases:
            with self.subTest(subject=subject, branch=branch):
                self.assertEqual(ex._extract_task_ids(subject, branch), expected)


class TestRunCapture(unittest.TestCase):
    def test_missing_binary_returns_none(self) -> None:
        with patch.object(ex.subprocess, "run", _fake_run([], missing=("git",))):
            self.assertIsNone(ex._run_capture(["git", "log"]))

    def test_nonzero_exit_returns_none(self) -> None:
        with patch.object(ex.subprocess, "run", _fake_run([(_has("log"), None)])):
            self.assertIsNone(ex._run_capture(["git", "log"]))

    def test_success_returns_stripped_stdout(self) -> None:
        with patch.object(ex.subprocess, "run", _fake_run([(_has("log"), "  x\n")])):
            self.assertEqual(ex._run_capture(["git", "log"]), "x")


class TestSyncGitLog(unittest.TestCase):
    def _log(self) -> str:
        return "\n".join(
            [
                _SEP.join(["sha1", "2026-07-01T10:00:00Z", "first #123", "HEAD -> m"]),
                # Undecorated commit: git omits the trailing separator (3 fields).
                _SEP.join(["sha2", "2026-07-01T10:05:00Z", "second #123"]),
            ]
        )

    def _routes(self):
        return [
            (_has("config", "user.email"), "dev@example.com"),
            (_has("log"), self._log()),
            (_has("remote", "get-url"), "git@github.com:o/r.git"),
        ]

    def test_happy_path_inserts_commit_events(self) -> None:
        state = _tmp_state()
        with patch.object(ex.subprocess, "run", _fake_run(self._routes())):
            result = ex.sync_git_log(state)
        self.assertEqual(result, {"inserted": 2})
        events = state.get_events()
        self.assertEqual([e.source for e in events], ["commit", "commit"])
        self.assertEqual({e.external_id for e in events}, {"git:sha1", "git:sha2"})
        self.assertEqual(events[0].repo, "o/r")
        self.assertEqual(events[0].task_ids, ["123"])
        self.assertEqual(events[0].timestamp.tzinfo, timezone.utc)

    def test_second_run_is_idempotent(self) -> None:
        state = _tmp_state()
        with patch.object(ex.subprocess, "run", _fake_run(self._routes())):
            ex.sync_git_log(state)
            second = ex.sync_git_log(state)
        self.assertEqual(second, {"inserted": 0})
        self.assertEqual(state.count_events(), 2)

    def test_git_missing_skips(self) -> None:
        state = _tmp_state()
        with patch.object(ex.subprocess, "run", _fake_run([], missing=("git",))):
            result = ex.sync_git_log(state)
        self.assertEqual(result, {"skipped": "git unavailable or user.email unset"})

    def test_git_log_failure_skips(self) -> None:
        state = _tmp_state()
        routes = [(_has("config", "user.email"), "dev@example.com"), (_has("log"), None)]
        with patch.object(ex.subprocess, "run", _fake_run(routes)):
            result = ex.sync_git_log(state)
        self.assertEqual(result, {"skipped": "git log failed"})

    def test_label_falls_back_to_empty_without_remote(self) -> None:
        state = _tmp_state()
        routes = [
            (_has("config", "user.email"), "dev@example.com"),
            (_has("log"), self._log()),
            (_has("remote", "get-url"), None),  # no origin remote
        ]
        with patch.object(ex.subprocess, "run", _fake_run(routes)):
            ex.sync_git_log(state)
        self.assertEqual(state.get_events()[0].repo, "")


class TestSyncGithub(unittest.TestCase):
    _PRS = '[{"number": 7, "title": "PR #123", "mergedAt": "2026-07-02T09:00:00Z", "headRefName": "feat/x"}]'
    _REVIEWS = (
        '[{"id": 55, "user": {"login": "octocat"}, "submitted_at": "2026-07-02T10:00:00Z"},'
        ' {"id": 56, "user": {"login": "someone-else"}, "submitted_at": "2026-07-02T11:00:00Z"}]'
    )

    def _routes(self, reviews=None):
        return [
            (_has("api", "user"), "octocat"),
            (_has("pr", "list"), self._PRS),
            (_has("repo", "view"), "o/r"),
            (_last("reviews"), reviews if reviews is not None else self._REVIEWS),
            (_has("remote", "get-url"), "git@github.com:o/r.git"),
        ]

    def test_happy_path_stores_merge_and_own_reviews(self) -> None:
        state = _tmp_state()
        with patch.object(ex.subprocess, "run", _fake_run(self._routes())):
            result = ex.sync_github(state)
        # One merge + one authored review (the other reviewer is filtered out).
        self.assertEqual(result, {"inserted": 2})
        by_source = {e.source: e for e in state.get_events()}
        self.assertEqual(by_source["merge"].external_id, "gh:pr:7")
        self.assertEqual(by_source["merge"].pr_num, 7)
        self.assertEqual(by_source["review"].external_id, "gh:review:55")
        self.assertEqual(by_source["review"].task_ids, ["123"])

    def test_second_run_is_idempotent(self) -> None:
        state = _tmp_state()
        with patch.object(ex.subprocess, "run", _fake_run(self._routes())):
            ex.sync_github(state)
            second = ex.sync_github(state)
        self.assertEqual(second, {"inserted": 0})

    def test_gh_missing_skips(self) -> None:
        state = _tmp_state()
        with patch.object(ex.subprocess, "run", _fake_run([], missing=("gh",))):
            result = ex.sync_github(state)
        self.assertEqual(result, {"skipped": "gh unavailable or not authenticated"})

    def test_pr_list_failure_skips(self) -> None:
        state = _tmp_state()
        routes = [(_has("api", "user"), "octocat"), (_has("pr", "list"), None)]
        with patch.object(ex.subprocess, "run", _fake_run(routes)):
            result = ex.sync_github(state)
        self.assertEqual(result, {"skipped": "gh pr list failed"})

    def test_reviews_skipped_when_slug_unresolved(self) -> None:
        state = _tmp_state()
        routes = [
            (_has("api", "user"), "octocat"),
            (_has("pr", "list"), self._PRS),
            (_has("repo", "view"), None),  # slug lookup fails
            (_has("remote", "get-url"), "git@github.com:o/r.git"),
        ]
        with patch.object(ex.subprocess, "run", _fake_run(routes)):
            result = ex.sync_github(state)
        # Only the merge event is stored; reviews are a clean no-op.
        self.assertEqual(result, {"inserted": 1})
        self.assertEqual([e.source for e in state.get_events()], ["merge"])

    def test_empty_reviews_list_stores_only_merge(self) -> None:
        state = _tmp_state()
        with patch.object(ex.subprocess, "run", _fake_run(self._routes(reviews="[]"))):
            result = ex.sync_github(state)
        self.assertEqual(result, {"inserted": 1})


class _FakeClient:
    """Structural stand-in for the OdooClient the chatter puller uses."""

    def __init__(self, messages=None, error=False, uid=5, partner=42):
        self.uid = uid
        self._partner = partner
        self._messages = messages or []
        self._error = error
        self.calls = []

    def execute(self, model, method, *args, **kwargs):
        self.calls.append((model, method, args, kwargs))
        if self._error:
            raise OdooError("odoo down")
        if model == "res.users":
            return [{"partner_id": [self._partner, "Dev"]}]
        return self._messages


class TestSyncOdooChatter(unittest.TestCase):
    def _state_with_task(self, task_id=123) -> LocalStateClient:
        state = _tmp_state()
        state.create_run(
            task_id=task_id, task_name="T", project_id=1, project_name="P"
        )
        return state

    def _messages(self):
        return [
            {"id": 900, "res_id": 123, "date": "2026-07-03 08:00:00", "subject": "note"},
            {"id": 901, "res_id": 123, "date": "2026-07-03 09:00:00", "subject": False},
        ]

    def test_happy_path_stores_chatter_events(self) -> None:
        state = self._state_with_task()
        client = _FakeClient(messages=self._messages())
        with patch.object(ex.subprocess, "run", _fake_run([(_has("remote"), None)])):
            result = ex.sync_odoo_chatter(client, state)
        self.assertEqual(result, {"inserted": 2})
        events = state.get_events()
        self.assertEqual({e.external_id for e in events}, {"odoo:mail:900", "odoo:mail:901"})
        self.assertEqual(events[0].source, "chatter")
        self.assertEqual(events[0].task_ids, ["123"])
        self.assertEqual(events[0].timestamp.tzinfo, timezone.utc)
        # The mail.message search is scoped to the authenticated partner id.
        search = next(c for c in client.calls if c[0] == "mail.message")
        domain = search[2][0]
        self.assertIn(("author_id", "=", 42), domain)
        self.assertIn(("res_id", "in", [123]), domain)

    def test_second_run_is_idempotent(self) -> None:
        state = self._state_with_task()
        client = _FakeClient(messages=self._messages())
        with patch.object(ex.subprocess, "run", _fake_run([(_has("remote"), None)])):
            ex.sync_odoo_chatter(client, state)
            second = ex.sync_odoo_chatter(client, state)
        self.assertEqual(second, {"inserted": 0})

    def test_no_tracked_tasks_skips(self) -> None:
        state = _tmp_state()
        client = _FakeClient(messages=self._messages())
        result = ex.sync_odoo_chatter(client, state)
        self.assertEqual(result, {"inserted": 0, "skipped": "no tracked tasks"})
        self.assertEqual(client.calls, [])  # Odoo never contacted.

    def test_odoo_error_skips(self) -> None:
        state = self._state_with_task()
        client = _FakeClient(error=True)
        with patch.object(ex.subprocess, "run", _fake_run([(_has("remote"), None)])):
            result = ex.sync_odoo_chatter(client, state)
        self.assertEqual(result, {"skipped": "odoo unavailable"})


class TestParsingAndGuards(unittest.TestCase):
    """Direct coverage of the small parsing/guard helpers, both branches."""

    def test_parse_iso_utc_naive_treated_as_utc(self) -> None:
        parsed = ex._parse_iso_utc("2026-07-01T10:00:00")  # no offset
        self.assertEqual(parsed.tzinfo, timezone.utc)
        self.assertEqual(parsed.hour, 10)

    def test_parse_iso_utc_offset_converted_to_utc(self) -> None:
        parsed = ex._parse_iso_utc("2026-07-01T10:00:00-04:00")
        self.assertEqual(parsed.hour, 14)  # +4 to UTC

    def test_store_commit_skips_malformed_line(self) -> None:
        state = _tmp_state()
        # Fewer than three fields is malformed and stored as nothing.
        self.assertEqual(ex._store_commit(state, "just-a-sha", "o/r"), 0)
        self.assertEqual(state.count_events(), 0)

    def test_gh_json_returns_none_on_bad_json(self) -> None:
        with patch.object(ex.subprocess, "run", _fake_run([(_has("api"), "not json")])):
            self.assertIsNone(ex._gh_json(["gh", "api", "x"]))

    def test_store_pr_skips_unmerged(self) -> None:
        state = _tmp_state()
        self.assertEqual(ex._store_pr(state, {"number": 1, "mergedAt": None}, "o/r"), 0)

    def test_store_review_skips_without_submitted_at(self) -> None:
        state = _tmp_state()
        pr = {"number": 1, "title": "t", "headRefName": "b"}
        review = {"id": 9, "submitted_at": None}
        self.assertEqual(ex._store_review(state, pr, review, "o/r"), 0)

    def test_current_partner_id_raises_on_empty_read(self) -> None:
        client = _FakeClient()
        client._messages = []  # unused here

        class _Empty(_FakeClient):
            def execute(self, model, method, *a, **k):
                return []  # res.users read returns nothing

        with self.assertRaises(OdooError):
            ex._current_partner_id(_Empty())

    def test_store_message_skips_falsy_date(self) -> None:
        state = _tmp_state()
        message = {"id": 1, "res_id": 5, "date": False, "subject": "x"}
        self.assertEqual(ex._store_message(state, message, "o/r"), 0)
        self.assertEqual(state.count_events(), 0)


class TestRepoLabel(unittest.TestCase):
    def test_persisted_label_wins_over_git(self) -> None:
        state = _tmp_state()
        state.set_setting("repo_label", "acme/widgets")
        # subprocess must never be consulted when the label is on record.
        with patch.object(ex.subprocess, "run", _fake_run([])):
            self.assertEqual(ex._current_repo_label(state), "acme/widgets")


if __name__ == "__main__":
    unittest.main()
