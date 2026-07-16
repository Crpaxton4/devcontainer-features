"""Tests for the idempotent resync pullers (issues #328, #378).

Every backing tool is faked: git/gh go through a fake ``subprocess.run`` that
dispatches on the command, and Odoo goes through a structural fake client. No
network, no live Odoo are involved. One real-git fixture test exercises the
``--all`` unmerged-branch capture (issue #378 item 2) end to end.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from odoo_sdk.adapters import external_sync as ex
from odoo_sdk.state import LocalConfig, LocalStateClient
from odoo_sdk.transport.errors import OdooError
from tests.support import make_state_db

_SEP = "\x1f"
_NOW = datetime(2026, 7, 15, tzinfo=timezone.utc)  # pins the resync window in tests


def _tmp_state() -> LocalStateClient:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return make_state_db(Path(tmp.name))


def _config(**behavior) -> LocalConfig:
    """Build a LocalConfig with explicit behavior overrides (no file/env)."""
    return LocalConfig(behavior=behavior)


class _FakeProc:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout


def _fake_run(routes, missing=()):
    """Return a ``subprocess.run`` stand-in dispatching on the command.

    ``routes`` maps a matcher (callable ``cmd -> bool``) to a stdout string, or to
    ``None`` to simulate a non-zero exit. Binaries named in ``missing`` raise
    ``FileNotFoundError`` (tool not installed).
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


class _TaskValidator:
    """Structural Odoo client answering only the ``project.task`` existence check."""

    def __init__(self, existing) -> None:
        self.existing = {int(i) for i in existing}
        self.calls = 0

    def execute(self, model, method, domain, fields=None):
        assert model == "project.task" and method == "search_read"
        self.calls += 1
        ids = domain[0][2]
        return [{"id": i} for i in ids if i in self.existing]


class TestExtractTaskIds(unittest.TestCase):
    """Table test for the widened, magnitude-gated task-id extractor (issue #378)."""

    def test_extraction_table(self) -> None:
        cases = [
            # New dominant conventions.
            ("", "24648#send-print", ["24648"]),  # branch prefix NNNNN#slug
            ("feat (task 24648)", "", ["24648"]),  # PR-title (task NNNNN)
            ("feat task-24648", "", ["24648"]),  # hyphen form
            ("done (24648)", "", ["24648"]),  # trailing (NNNNN)
            # Retained forms, now magnitude-gated.
            ("Fix #12345", "", ["12345"]),
            ("", "odoo-98765", ["98765"]),
            ("cleanup [55555]", "", ["55555"]),
            ("ODOO-24648 done", "", ["24648"]),  # case-insensitive
            ("#24648 and #24648 again", "", ["24648"]),  # de-duped
            # False positives that must now mint NOTHING.
            ("#31 - Hardcode Checks", "", []),  # short client-side number
            ("cross ref (#189)", "", []),  # PR cross-reference
            ("bumped to v2", "main", []),  # nothing extractable
        ]
        for subject, branch, expected in cases:
            with self.subTest(subject=subject, branch=branch):
                self.assertEqual(ex._extract_task_ids(subject, branch), expected)


class TestTaskIdValidation(unittest.TestCase):
    """The batched project.task existence check + weak-flag payload (item 1)."""

    def test_validate_returns_existing_subset(self) -> None:
        client = _TaskValidator([24648])
        self.assertEqual(ex._validate_task_ids(client, {"24648", "99999"}), {"24648"})
        self.assertEqual(client.calls, 1)

    def test_validate_without_client_returns_none(self) -> None:
        self.assertIsNone(ex._validate_task_ids(None, {"24648"}))

    def test_validate_no_numeric_ids_returns_empty(self) -> None:
        client = _TaskValidator([24648])
        self.assertEqual(ex._validate_task_ids(client, set()), set())

    def test_validate_odoo_error_returns_none(self) -> None:
        class _Boom:
            def execute(self, *a, **k):
                raise OdooError("down")

        self.assertIsNone(ex._validate_task_ids(_Boom(), {"24648"}))

    def test_finalize_flags_unknown_ids_out_of_task_ids(self) -> None:
        event = ex.EventRecord(
            id=None, source="commit", timestamp=_NOW,
            task_ids=["24648", "99999"], repo="o/r",
        )
        ex._finalize_task_attribution(event, {"24648"})
        self.assertEqual(event.task_ids, ["24648"])
        self.assertEqual(event.payload, {"unvalidated_task_ids": ["99999"]})

    def test_finalize_no_validation_keeps_ids(self) -> None:
        event = ex.EventRecord(
            id=None, source="commit", timestamp=_NOW,
            task_ids=["99999"], repo="o/r",
        )
        ex._finalize_task_attribution(event, None)  # validation did not run
        self.assertEqual(event.task_ids, ["99999"])
        self.assertIsNone(event.payload)


class TestSyncGitLog(unittest.TestCase):
    def _log(self) -> str:
        return "\n".join(
            [
                _SEP.join(["sha1", "2026-07-01T10:00:00Z", "first (task 24648)", "HEAD -> m"]),
                # Undecorated commit: git omits the trailing separator (3 fields).
                _SEP.join(["sha2", "2026-07-01T10:05:00Z", "second (task 24648)"]),
            ]
        )

    def _routes(self, log=None):
        return [
            (_has("config", "user.email"), "dev@example.com"),
            (_has("log"), log if log is not None else self._log()),
            (_has("remote", "get-url"), "git@github.com:o/r.git"),
        ]

    def test_happy_path_inserts_commit_events(self) -> None:
        state = _tmp_state()
        with patch.object(ex.subprocess, "run", _fake_run(self._routes())):
            result = ex.sync_git_log(state, _config(), now=_NOW)
        self.assertEqual(result, {"inserted": 2})
        events = state.get_events()
        self.assertEqual([e.source for e in events], ["commit", "commit"])
        self.assertEqual({e.external_id for e in events}, {"git:sha1", "git:sha2"})
        self.assertEqual(events[0].repo, "o/r")
        self.assertEqual(events[0].task_ids, ["24648"])
        self.assertEqual(events[0].timestamp.tzinfo, timezone.utc)

    def test_log_command_uses_all_and_since(self) -> None:
        captured = {}

        def _capture(cmd):
            if "log" in cmd:
                captured["cmd"] = cmd
                return self._log()
            return {"config": "dev@example.com", "remote": "git@github.com:o/r.git"}[
                "remote" if "remote" in cmd else "config"
            ]

        with patch.object(ex, "_run_capture", _capture):
            ex.sync_git_log(state=_tmp_state(), config=_config(resync_window_days=14), now=_NOW)
        self.assertIn("--all", captured["cmd"])
        self.assertIn("--since=2026-07-01", captured["cmd"])  # 14 days before 07-15

    def test_multiple_author_emails_are_or_ed(self) -> None:
        state = _tmp_state()
        both = lambda cmd: (
            "log" in cmd
            and "--author=a@x.com" in cmd
            and "--author=b@y.com" in cmd
        )
        routes = [
            (both, self._log()),
            (_has("remote", "get-url"), None),
        ]
        cfg = _config(resync_authors="a@x.com, b@y.com")
        with patch.object(ex.subprocess, "run", _fake_run(routes)):
            result = ex.sync_git_log(state, cfg, now=_NOW)
        self.assertEqual(result, {"inserted": 2})

    def test_validation_flags_unknown_task_id(self) -> None:
        state = _tmp_state()
        log = "\n".join(
            [
                _SEP.join(["shaA", "2026-07-01T10:00:00Z", "real (task 24648)"]),
                _SEP.join(["shaB", "2026-07-01T10:05:00Z", "bogus (task 99999)"]),
            ]
        )
        client = _TaskValidator([24648])
        with patch.object(ex.subprocess, "run", _fake_run(self._routes(log=log))):
            result = ex.sync_git_log(state, _config(), client, now=_NOW)
        self.assertEqual(result, {"inserted": 2})
        by_ext = {e.external_id: e for e in state.get_events()}
        self.assertEqual(by_ext["git:shaA"].task_ids, ["24648"])
        self.assertIsNone(by_ext["git:shaA"].payload)
        self.assertEqual(by_ext["git:shaB"].task_ids, [])  # not billed
        self.assertEqual(by_ext["git:shaB"].payload, {"unvalidated_task_ids": ["99999"]})
        self.assertEqual(client.calls, 1)  # ONE batched check for the whole puller

    def test_second_run_is_idempotent(self) -> None:
        state = _tmp_state()
        with patch.object(ex.subprocess, "run", _fake_run(self._routes())):
            ex.sync_git_log(state, _config(), now=_NOW)
            second = ex.sync_git_log(state, _config(), now=_NOW)
        self.assertEqual(second, {"inserted": 0})
        self.assertEqual(state.count_events(), 2)

    def test_git_missing_skips(self) -> None:
        state = _tmp_state()
        with patch.object(ex.subprocess, "run", _fake_run([], missing=("git",))):
            result = ex.sync_git_log(state, _config(), now=_NOW)
        self.assertEqual(result, {"skipped": "git unavailable or user.email unset"})

    def test_git_log_failure_skips(self) -> None:
        state = _tmp_state()
        routes = [(_has("config", "user.email"), "dev@example.com"), (_has("log"), None)]
        with patch.object(ex.subprocess, "run", _fake_run(routes)):
            result = ex.sync_git_log(state, _config(), now=_NOW)
        self.assertEqual(result, {"skipped": "git log failed"})

    def test_label_falls_back_to_empty_without_remote(self) -> None:
        state = _tmp_state()
        routes = [
            (_has("config", "user.email"), "dev@example.com"),
            (_has("log"), self._log()),
            (_has("remote", "get-url"), None),  # no origin remote
        ]
        with patch.object(ex.subprocess, "run", _fake_run(routes)):
            ex.sync_git_log(state, _config(), now=_NOW)
        self.assertEqual(state.get_events()[0].repo, "")


@unittest.skipUnless(shutil.which("git"), "git not installed")
class TestGitAllFlagIntegration(unittest.TestCase):
    """Real-git fixture: ``--all`` captures a commit on an unmerged branch (#378 #2)."""

    def _git(self, *args, cwd):
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)

    def test_all_sees_unmerged_branch_commit(self) -> None:
        with tempfile.TemporaryDirectory() as repo:
            self._git("init", "-q", "-b", "main", cwd=repo)
            self._git("config", "user.email", "dev@example.com", cwd=repo)
            self._git("config", "user.name", "Dev", cwd=repo)
            (Path(repo) / "a.txt").write_text("a")
            self._git("add", "-A", cwd=repo)
            self._git("commit", "--no-verify", "-qm", "base (task 24648)", cwd=repo)
            self._git("checkout", "-q", "-b", "feature", cwd=repo)
            (Path(repo) / "b.txt").write_text("b")
            self._git("add", "-A", cwd=repo)
            self._git("commit", "--no-verify", "-qm", "wip (task 24648)", cwd=repo)
            # Back on main: the feature commit is NOT an ancestor of HEAD.
            self._git("checkout", "-q", "main", cwd=repo)

            state = _tmp_state()
            prev = os.getcwd()
            os.chdir(repo)
            try:
                result = ex.sync_git_log(state, _config(), now=_NOW)
            finally:
                os.chdir(prev)

        # Two commits: the base AND the unmerged-branch commit, thanks to --all.
        self.assertEqual(result["inserted"], 2)
        self.assertEqual({e.source for e in state.get_events()}, {"commit"})


class TestSyncGithub(unittest.TestCase):
    _PRS = (
        '[{"number": 7, "title": "PR (task 24648)", "state": "MERGED",'
        ' "mergedAt": "2026-07-02T09:00:00Z", "createdAt": "2026-07-01T09:00:00Z",'
        ' "headRefName": "24648#feat"},'
        ' {"number": 8, "title": "Open PR (task 55555)", "state": "OPEN",'
        ' "mergedAt": null, "createdAt": "2026-07-03T09:00:00Z",'
        ' "headRefName": "55555#wip"}]'
    )
    _OWN_REVIEWS = (
        '[{"id": 55, "user": {"login": "octocat"}, "submitted_at": "2026-07-02T10:00:00Z"},'
        ' {"id": 56, "user": {"login": "someone-else"}, "submitted_at": "2026-07-02T11:00:00Z"}]'
    )
    _REVIEWED_PRS = (
        '[{"number": 3, "title": "Others PR (task 33333)",'
        ' "repository": {"nameWithOwner": "other/repo"}}]'
    )
    _OTHER_REVIEWS = (
        '[{"id": 77, "user": {"login": "octocat"}, "submitted_at": "2026-07-05T12:00:00Z"}]'
    )
    _COMMENTED = (
        '[{"number": 9, "title": "Issue (task 44444)",'
        ' "repository": {"nameWithOwner": "other/repo"}}]'
    )
    _COMMENTS = (
        '[{"id": 111, "user": {"login": "octocat"}, "created_at": "2026-07-06T08:00:00Z"}]'
    )

    def _routes(self):
        return [
            (_has("api", "user"), "octocat"),
            (_has("repo", "view"), "o/r"),
            (_has("pr", "list"), self._PRS),
            (lambda c: c[-1] == "repos/o/r/pulls/7/reviews", self._OWN_REVIEWS),
            (lambda c: c[-1] == "repos/o/r/pulls/8/reviews", "[]"),
            (_has("search", "prs"), self._REVIEWED_PRS),
            (lambda c: c[-1] == "repos/other/repo/pulls/3/reviews", self._OTHER_REVIEWS),
            (_has("search", "issues"), self._COMMENTED),
            (lambda c: c[-1] == "repos/other/repo/issues/9/comments", self._COMMENTS),
            (_has("remote", "get-url"), "git@github.com:o/r.git"),
        ]

    def test_captures_prs_reviews_and_comments(self) -> None:
        state = _tmp_state()
        with patch.object(ex.subprocess, "run", _fake_run(self._routes())):
            result = ex.sync_github(state, _config(), now=_NOW)
        # merge(7) + merge(8, opened) + own review(55) + others review(77) + comment(111)
        self.assertEqual(result, {"inserted": 5})
        by_ext = {e.external_id: e for e in state.get_events()}
        self.assertEqual(set(by_ext), {
            "gh:pr:7", "gh:pr:8", "gh:review:55", "gh:review:77", "gh:comment:111",
        })
        # Opened (unmerged) PR is captured via --state all, timestamped at createdAt.
        self.assertEqual(by_ext["gh:pr:8"].task_ids, ["55555"])
        # Comment is a new source, attributed via the issue title, on its own repo.
        comment = by_ext["gh:comment:111"]
        self.assertEqual(comment.source, "comment")
        self.assertEqual(comment.repo, "other/repo")
        self.assertEqual(comment.task_ids, ["44444"])
        # Review on someone else's PR is attributed and stored against that repo.
        self.assertEqual(by_ext["gh:review:77"].repo, "other/repo")
        # A review by a different user on our PR is never attributed to us.
        self.assertNotIn("gh:review:56", by_ext)

    def test_out_of_window_events_skipped(self) -> None:
        state = _tmp_state()
        with patch.object(ex.subprocess, "run", _fake_run(self._routes())):
            # A 3-day window ends before every July artifact here.
            result = ex.sync_github(state, _config(resync_window_days=3), now=_NOW)
        self.assertEqual(result, {"inserted": 0})

    def test_two_identities_both_captured(self) -> None:
        state = _tmp_state()
        prs_for = {
            "octo-a": '[{"number": 1, "title": "A (task 24648)", "state": "MERGED",'
            ' "mergedAt": "2026-07-02T09:00:00Z", "createdAt": "2026-07-01T09:00:00Z",'
            ' "headRefName": "24648#a"}]',
            "octo-b": '[{"number": 2, "title": "B (task 55555)", "state": "MERGED",'
            ' "mergedAt": "2026-07-02T09:00:00Z", "createdAt": "2026-07-01T09:00:00Z",'
            ' "headRefName": "55555#b"}]',
        }
        routes = [
            (_has("api", "user"), "octo-a"),
            (_has("repo", "view"), "o/r"),
            (lambda c: "list" in c and "octo-a" in c, prs_for["octo-a"]),
            (lambda c: "list" in c and "octo-b" in c, prs_for["octo-b"]),
            (lambda c: c[-1].endswith("/reviews"), "[]"),
            (_has("search", "prs"), "[]"),
            (_has("search", "issues"), "[]"),
            (_has("remote", "get-url"), "git@github.com:o/r.git"),
        ]
        cfg = _config(resync_authors="octo-a octo-b")
        with patch.object(ex.subprocess, "run", _fake_run(routes)):
            result = ex.sync_github(state, cfg, now=_NOW)
        self.assertEqual(result, {"inserted": 2})
        self.assertEqual(
            {e.external_id for e in state.get_events()}, {"gh:pr:1", "gh:pr:2"}
        )

    def test_search_results_without_repository_are_skipped(self) -> None:
        state = _tmp_state()
        routes = [
            (_has("api", "user"), "octocat"),
            (_has("repo", "view"), "o/r"),
            (_has("pr", "list"), "[]"),
            (_has("search", "prs"), '[{"number": 3, "title": "no repo"}]'),
            (_has("search", "issues"), '[{"number": 9, "title": "no repo"}]'),
            (_has("remote", "get-url"), "git@github.com:o/r.git"),
        ]
        with patch.object(ex.subprocess, "run", _fake_run(routes)):
            result = ex.sync_github(state, _config(), now=_NOW)
        self.assertEqual(result, {"inserted": 0})

    def test_second_run_is_idempotent(self) -> None:
        state = _tmp_state()
        with patch.object(ex.subprocess, "run", _fake_run(self._routes())):
            ex.sync_github(state, _config(), now=_NOW)
            second = ex.sync_github(state, _config(), now=_NOW)
        self.assertEqual(second, {"inserted": 0})

    def test_gh_missing_skips(self) -> None:
        state = _tmp_state()
        with patch.object(ex.subprocess, "run", _fake_run([], missing=("gh",))):
            result = ex.sync_github(state, _config(), now=_NOW)
        self.assertEqual(result, {"skipped": "gh unavailable or not authenticated"})

    def test_pr_list_failure_skips(self) -> None:
        state = _tmp_state()
        routes = [
            (_has("api", "user"), "octocat"),
            (_has("repo", "view"), "o/r"),
            (_has("pr", "list"), None),
            (_has("remote", "get-url"), "git@github.com:o/r.git"),
        ]
        with patch.object(ex.subprocess, "run", _fake_run(routes)):
            result = ex.sync_github(state, _config(), now=_NOW)
        self.assertEqual(result, {"skipped": "gh pr list failed"})

    def test_reviews_skipped_when_slug_unresolved(self) -> None:
        state = _tmp_state()
        prs = (
            '[{"number": 7, "title": "PR (task 24648)", "state": "MERGED",'
            ' "mergedAt": "2026-07-02T09:00:00Z", "createdAt": "2026-07-01T09:00:00Z",'
            ' "headRefName": "24648#feat"}]'
        )
        routes = [
            (_has("api", "user"), "octocat"),
            (_has("repo", "view"), None),  # slug lookup fails
            (_has("pr", "list"), prs),
            (_has("search", "prs"), "[]"),
            (_has("search", "issues"), "[]"),
            (_has("remote", "get-url"), "git@github.com:o/r.git"),
        ]
        with patch.object(ex.subprocess, "run", _fake_run(routes)):
            result = ex.sync_github(state, _config(), now=_NOW)
        # Only the merge event is stored; own-PR reviews are a clean no-op.
        self.assertEqual(result, {"inserted": 1})
        self.assertEqual([e.source for e in state.get_events()], ["merge"])


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
    def _messages(self):
        return [
            {"id": 900, "res_id": 123, "date": "2026-07-03 08:00:00", "subject": "note"},
            {"id": 901, "res_id": 777, "date": "2026-07-03 09:00:00", "subject": False},
        ]

    def test_author_wide_search_stores_chatter(self) -> None:
        # No tracked tasks at all — the author-wide search still finds work on
        # tasks never started locally (issue #378 item 5).
        state = _tmp_state()
        client = _FakeClient(messages=self._messages())
        with patch.object(ex.subprocess, "run", _fake_run([(_has("remote"), None)])):
            result = ex.sync_odoo_chatter(client, state, _config(), now=_NOW)
        self.assertEqual(result, {"inserted": 2})
        events = state.get_events()
        self.assertEqual({e.external_id for e in events}, {"odoo:mail:900", "odoo:mail:901"})
        self.assertEqual({e.task_ids[0] for e in events}, {"123", "777"})
        # The search is author-scoped and date-bounded, NOT res_id-scoped.
        search = next(c for c in client.calls if c[0] == "mail.message")
        domain = search[2][0]
        self.assertIn(("author_id", "=", 42), domain)
        self.assertIn(("date", ">=", "2026-06-15 00:00:00"), domain)
        self.assertIn(("date", "<=", "2026-07-15 00:00:00"), domain)
        self.assertFalse(any(term[0] == "res_id" for term in domain))

    def test_second_run_is_idempotent(self) -> None:
        state = _tmp_state()
        client = _FakeClient(messages=self._messages())
        with patch.object(ex.subprocess, "run", _fake_run([(_has("remote"), None)])):
            ex.sync_odoo_chatter(client, state, _config(), now=_NOW)
            second = ex.sync_odoo_chatter(client, state, _config(), now=_NOW)
        self.assertEqual(second, {"inserted": 0})

    def test_odoo_error_skips(self) -> None:
        state = _tmp_state()
        client = _FakeClient(error=True)
        with patch.object(ex.subprocess, "run", _fake_run([(_has("remote"), None)])):
            result = ex.sync_odoo_chatter(client, state, _config(), now=_NOW)
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

    def test_build_commit_event_skips_malformed_line(self) -> None:
        self.assertIsNone(ex._build_commit_event("just-a-sha", "o/r"))

    def test_ts_in_window_bounds(self) -> None:
        since = datetime(2026, 7, 1, tzinfo=timezone.utc)
        self.assertEqual(
            ex._ts_in_window("2026-07-02T00:00:00Z", since),
            datetime(2026, 7, 2, tzinfo=timezone.utc),
        )
        self.assertIsNone(ex._ts_in_window("2026-06-30T00:00:00Z", since))
        self.assertIsNone(ex._ts_in_window(None, since))
        self.assertIsNone(ex._ts_in_window("not-a-date", since))

    def test_gh_json_returns_none_on_bad_json(self) -> None:
        with patch.object(ex.subprocess, "run", _fake_run([(_has("api"), "not json")])):
            self.assertIsNone(ex._gh_json(["gh", "api", "x"]))

    def test_pr_event_skips_without_timestamp(self) -> None:
        ctx = ex._GithubCtx(label="o/r", slug="o/r", since=_NOW.replace(year=2026, month=1))
        self.assertIsNone(ex._pr_event({"number": 1, "mergedAt": None}, ctx))

    def test_review_event_skips_without_submitted_at(self) -> None:
        pr = {"number": 1, "title": "t", "headRefName": "b"}
        review = {"id": 9, "submitted_at": None}
        since = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.assertIsNone(ex._review_event(review, pr, "o/r", since))

    def test_current_partner_id_raises_on_empty_read(self) -> None:
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
