"""Tests for the idempotent resync pullers (issues #328, #378, #652, #653).

Every backing tool is faked: git/gh go through a fake ``subprocess.run`` that
dispatches on the command, and Odoo goes through a structural fake client. No
network, no live Odoo are involved. Two real-git fixture tests exercise the
``--all`` unmerged-branch capture (issue #378 item 2) and the recursive
multi-repo discovery (#652) end to end.
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import date, datetime, timezone
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


def _repo_cmd(path, *needles):
    """Match a ``git -C <path> ...`` command scoped to one discovered repo."""
    return lambda cmd: (
        "-C" in cmd
        and cmd[cmd.index("-C") + 1] == path
        and all(n in cmd for n in needles)
    )


def _pr_view(number, slug):
    """Match the per-PR detail fetch ``gh pr view <number> -R <slug>``."""
    return _has("pr", "view", str(number), slug)


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
            ("", "24648-send-print", ["24648"]),  # branch prefix NNNNN-slug (#622)
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
            ("", "24648#send-print", []),  # retired legacy NNNNN#slug (#622 cutover)
            ("#31 - Hardcode Checks", "", []),  # short client-side number
            ("cross ref (#189)", "", []),  # PR cross-reference
            ("bumped to v2", "main", []),  # nothing extractable
            # Bare leading id is GATED (issue #654): without ``allow_leading_id``
            # (the calendar/gmail contract) a leading id or year mints nothing.
            ("24648 rtv process", "", []),
            ("2026 Q1 planning", "", []),
        ]
        for subject, branch, expected in cases:
            with self.subTest(subject=subject, branch=branch):
                self.assertEqual(ex._extract_task_ids(subject, branch), expected)

    def test_leading_id_extraction_table(self) -> None:
        """The gated bare-leading-id form (issue #654) — git/GitHub paths only."""
        cases = [
            ("24648 rtv process", "", ["24648"]),  # space delimiter
            ("24648#OdooMeetingRecordingModel", "", ["24648"]),  # hash delimiter
            ("24648: fix widget", "", ["24648"]),  # colon delimiter
            ("24648-fix crash", "", ["24648"]),  # hyphen; overlaps <id>-slug form
            ("24648", "", ["24648"]),  # bare-only title: the join space delimits
            # Accepted: a leading year reads as an id; the online validation
            # layer (_validate_task_ids) drops ids that name no real task.
            ("2026 Q1 planning", "", ["2026"]),
            # Guards.
            ("333:IMP repo-local numbering", "", []),  # below _MIN_TASK_ID_DIGITS
            ("fix 24648 later", "", []),  # mid-string bare number: anchor holds
            ("", "24648 rtv process", []),  # branch is never at string start
        ]
        for subject, branch, expected in cases:
            with self.subTest(subject=subject, branch=branch):
                self.assertEqual(
                    ex._extract_task_ids(subject, branch, allow_leading_id=True),
                    expected,
                )


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


class TestDiscoverGitRepos(unittest.TestCase):
    """Recursive repo discovery (#652): any depth, gitfiles, minimal pruning."""

    def test_discovers_repos_at_any_depth_with_pruning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()  # the cwd itself is a repo
            (root / "a" / "proj1" / ".git").mkdir(parents=True)
            (root / "b" / "c" / "d" / "proj2" / ".git").mkdir(parents=True)
            # Worktree/submodule shape: ``.git`` is a FILE (gitfile), not a dir.
            (root / "wt").mkdir()
            (root / "wt" / ".git").write_text("gitdir: /elsewhere/.git/worktrees/wt\n")
            # Vendored tree: a checkout under node_modules must be pruned.
            (root / "node_modules" / "dep" / ".git").mkdir(parents=True)
            # A nested checkout BELOW another repo root is still found.
            (root / "a" / "proj1" / "vendor" / "sub" / ".git").mkdir(parents=True)

            repos = ex._discover_git_repos(root)

            self.assertEqual(
                repos,
                sorted(
                    [
                        root,
                        root / "a" / "proj1",
                        root / "a" / "proj1" / "vendor" / "sub",
                        root / "b" / "c" / "d" / "proj2",
                        root / "wt",
                    ]
                ),
            )

    def test_lone_checkout_yields_exactly_itself(self) -> None:
        # Running inside a single repo reproduces the pre-#652 behavior.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            (root / "src").mkdir()
            self.assertEqual(ex._discover_git_repos(root), [root])

    def test_no_repos_yields_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(ex._discover_git_repos(Path(tmp)), [])


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

    def _one_repo(self):
        return patch.object(ex, "_discover_git_repos", return_value=[Path("/repo")])

    def test_happy_path_inserts_commit_events(self) -> None:
        state = _tmp_state()
        with patch.object(ex.subprocess, "run", _fake_run(self._routes())), self._one_repo():
            result = ex.sync_git_log(state, _config(), now=_NOW)
        self.assertEqual(result, {"inserted": 2, "found": 2, "repos": 1})
        events = state.get_events()
        self.assertEqual([e.source for e in events], ["commit", "commit"])
        self.assertEqual({e.external_id for e in events}, {"git:sha1", "git:sha2"})
        self.assertEqual(events[0].repo, "o/r")
        self.assertEqual(events[0].task_ids, ["24648"])
        self.assertEqual(events[0].timestamp.tzinfo, timezone.utc)

    def _captured_log_cmd(self, config, **kwargs):
        captured = {}

        def _capture(cmd):
            if "log" in cmd:
                captured["cmd"] = cmd
                return self._log()
            return {"config": "dev@example.com", "remote": "git@github.com:o/r.git"}[
                "remote" if "remote" in cmd else "config"
            ]

        with patch.object(ex, "_run_capture", _capture), self._one_repo():
            ex.sync_git_log(state=_tmp_state(), config=config, now=_NOW, **kwargs)
        return captured["cmd"]

    def test_log_command_scopes_repo_with_full_iso_window(self) -> None:
        cmd = self._captured_log_cmd(_config(resync_window_days=14))
        self.assertEqual(cmd[cmd.index("-C") + 1], "/repo")
        self.assertIn("--all", cmd)
        # Full ISO bounds (#652), not the old date-only --since: 14 days back.
        self.assertIn("--since=2026-07-01T00:00:00+00:00", cmd)
        self.assertIn("--until=2026-07-15T00:00:00+00:00", cmd)

    def test_explicit_range_overrides_rolling_window(self) -> None:
        cmd = self._captured_log_cmd(
            _config(), start=date(2026, 6, 1), end=date(2026, 6, 30)
        )
        self.assertIn("--since=2026-06-01T00:00:00+00:00", cmd)
        # Inclusive end date: until is midnight of the day AFTER it.
        self.assertIn("--until=2026-07-01T00:00:00+00:00", cmd)

    def test_multiple_repos_each_get_their_own_label(self) -> None:
        state = _tmp_state()
        log_a = _SEP.join(["shaA", "2026-07-01T10:00:00Z", "a (task 24648)"])
        log_b = _SEP.join(["shaB", "2026-07-01T11:00:00Z", "b (task 24648)"])
        routes = [
            (_has("config", "user.email"), "dev@example.com"),
            (_repo_cmd("/a", "log"), log_a),
            (_repo_cmd("/b", "log"), log_b),
            (_repo_cmd("/a", "remote"), "git@github.com:acme/alpha.git"),
            (_repo_cmd("/b", "remote"), "git@github.com:acme/beta.git"),
        ]
        with patch.object(ex.subprocess, "run", _fake_run(routes)), patch.object(
            ex, "_discover_git_repos", return_value=[Path("/a"), Path("/b")]
        ):
            result = ex.sync_git_log(state, _config(), now=_NOW)
        self.assertEqual(result, {"inserted": 2, "found": 2, "repos": 2})
        labels = {e.external_id: e.repo for e in state.get_events()}
        self.assertEqual(labels, {"git:shaA": "acme/alpha", "git:shaB": "acme/beta"})

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
        with patch.object(ex.subprocess, "run", _fake_run(routes)), self._one_repo():
            result = ex.sync_git_log(state, cfg, now=_NOW)
        self.assertEqual(result["inserted"], 2)

    def test_validation_flags_unknown_task_id(self) -> None:
        state = _tmp_state()
        log = "\n".join(
            [
                _SEP.join(["shaA", "2026-07-01T10:00:00Z", "real (task 24648)"]),
                _SEP.join(["shaB", "2026-07-01T10:05:00Z", "bogus (task 99999)"]),
            ]
        )
        client = _TaskValidator([24648])
        with patch.object(
            ex.subprocess, "run", _fake_run(self._routes(log=log))
        ), self._one_repo():
            result = ex.sync_git_log(state, _config(), client, now=_NOW)
        self.assertEqual(result["inserted"], 2)
        by_ext = {e.external_id: e for e in state.get_events()}
        self.assertEqual(by_ext["git:shaA"].task_ids, ["24648"])
        self.assertIsNone(by_ext["git:shaA"].payload)
        self.assertEqual(by_ext["git:shaB"].task_ids, [])  # not billed
        self.assertEqual(by_ext["git:shaB"].payload, {"unvalidated_task_ids": ["99999"]})
        self.assertEqual(client.calls, 1)  # ONE batched check for the whole puller

    def test_second_run_is_idempotent(self) -> None:
        state = _tmp_state()
        with patch.object(ex.subprocess, "run", _fake_run(self._routes())), self._one_repo():
            ex.sync_git_log(state, _config(), now=_NOW)
            second = ex.sync_git_log(state, _config(), now=_NOW)
        # found stays 2 (the commits are still in the window); inserted drops
        # to 0 — the counters distinguish "nothing new" from "nothing found".
        self.assertEqual(second, {"inserted": 0, "found": 2, "repos": 1})
        self.assertEqual(state.count_events(), 2)

    def test_git_missing_is_error(self) -> None:
        state = _tmp_state()
        with patch.object(ex.subprocess, "run", _fake_run([], missing=("git",))):
            result = ex.sync_git_log(state, _config(), now=_NOW)
        self.assertEqual(result, {"error": "git unavailable or user.email unset"})

    def test_zero_repos_is_error(self) -> None:
        state = _tmp_state()
        routes = [(_has("config", "user.email"), "dev@example.com")]
        with patch.object(ex.subprocess, "run", _fake_run(routes)), patch.object(
            ex, "_discover_git_repos", return_value=[]
        ):
            result = ex.sync_git_log(state, _config(), now=_NOW)
        self.assertTrue(result["error"].startswith("no git repositories under "))

    def test_all_repos_failing_is_error(self) -> None:
        state = _tmp_state()
        routes = [(_has("config", "user.email"), "dev@example.com"), (_has("log"), None)]
        with patch.object(ex.subprocess, "run", _fake_run(routes)), self._one_repo():
            result = ex.sync_git_log(state, _config(), now=_NOW)
        self.assertEqual(result, {"error": "git log failed in all 1 repositories"})

    def test_one_failing_repo_only_counts_as_failed(self) -> None:
        state = _tmp_state()
        log_a = _SEP.join(["shaA", "2026-07-01T10:00:00Z", "a (task 24648)"])
        routes = [
            (_has("config", "user.email"), "dev@example.com"),
            (_repo_cmd("/a", "log"), log_a),
            (_repo_cmd("/b", "log"), None),  # this repo's log fails
            (_repo_cmd("/a", "remote"), "git@github.com:acme/alpha.git"),
        ]
        with patch.object(ex.subprocess, "run", _fake_run(routes)), patch.object(
            ex, "_discover_git_repos", return_value=[Path("/a"), Path("/b")]
        ):
            result = ex.sync_git_log(state, _config(), now=_NOW)
        self.assertEqual(
            result, {"inserted": 1, "found": 1, "repos": 2, "failed_repos": 1}
        )

    def test_label_falls_back_to_empty_without_remote(self) -> None:
        state = _tmp_state()
        routes = [
            (_has("config", "user.email"), "dev@example.com"),
            (_has("log"), self._log()),
            (_has("remote", "get-url"), None),  # no origin remote
        ]
        with patch.object(ex.subprocess, "run", _fake_run(routes)), self._one_repo():
            ex.sync_git_log(state, _config(), now=_NOW)
        self.assertEqual(state.get_events()[0].repo, "")

    def test_inverted_window_is_error(self) -> None:
        state = _tmp_state()
        routes = [(_has("config", "user.email"), "dev@example.com")]
        with patch.object(ex.subprocess, "run", _fake_run(routes)), self._one_repo():
            result = ex.sync_git_log(state, _config(), now=_NOW, end=date(2026, 1, 31))
        self.assertIn("empty resync window", result["error"])

    def test_linked_worktrees_collapse_to_one_repo(self) -> None:
        # A gitfile worktree shares the main clone's object store; reading both
        # would return every commit twice and inflate found/repos (#652).
        state = _tmp_state()
        routes = [
            (_has("config", "user.email"), "dev@example.com"),
            (_repo_cmd("/main", "rev-parse"), "/main/.git"),
            (_repo_cmd("/main/wt", "rev-parse"), "/main/.git"),  # shared store
            (_repo_cmd("/sub", "rev-parse"), "/main/.git/modules/sub"),  # submodule
            (_has("log"), self._log()),
            (_has("remote", "get-url"), "git@github.com:o/r.git"),
        ]
        with patch.object(ex.subprocess, "run", _fake_run(routes)), patch.object(
            ex,
            "_discover_git_repos",
            return_value=[Path("/main"), Path("/main/wt"), Path("/sub")],
        ):
            result = ex.sync_git_log(state, _config(), now=_NOW)
        # The worktree is collapsed; the submodule keeps its own store.
        self.assertEqual(result["repos"], 2)


@unittest.skipUnless(shutil.which("git"), "git not installed")
class TestGitAllFlagIntegration(unittest.TestCase):
    """Real-git fixtures: ``--all`` unmerged capture (#378 #2) + discovery (#652).

    These run against real repos created at test time, so they use the real
    clock (a pinned ``now`` would put ``--until`` before the commits).
    """

    def _git(self, *args, cwd):
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)

    def _init_repo(self, repo: Path, origin: str = "") -> None:
        self._git("init", "-q", "-b", "main", cwd=repo)
        self._git("config", "user.email", "dev@example.com", cwd=repo)
        self._git("config", "user.name", "Dev", cwd=repo)
        if origin:
            self._git("remote", "add", "origin", origin, cwd=repo)

    def _run_in(self, directory, config) -> tuple:
        state = _tmp_state()
        prev = os.getcwd()
        os.chdir(directory)
        try:
            return ex.sync_git_log(state, config), state
        finally:
            os.chdir(prev)

    def test_all_sees_unmerged_branch_commit(self) -> None:
        with tempfile.TemporaryDirectory() as repo:
            self._init_repo(Path(repo))
            (Path(repo) / "a.txt").write_text("a")
            self._git("add", "-A", cwd=repo)
            self._git("commit", "--no-verify", "-qm", "base (task 24648)", cwd=repo)
            self._git("checkout", "-q", "-b", "feature", cwd=repo)
            (Path(repo) / "b.txt").write_text("b")
            self._git("add", "-A", cwd=repo)
            self._git("commit", "--no-verify", "-qm", "wip (task 24648)", cwd=repo)
            # Back on main: the feature commit is NOT an ancestor of HEAD.
            self._git("checkout", "-q", "main", cwd=repo)

            result, state = self._run_in(repo, _config())

        # Two commits: the base AND the unmerged-branch commit, thanks to --all.
        self.assertEqual(result["inserted"], 2)
        self.assertEqual(result["repos"], 1)
        self.assertEqual({e.source for e in state.get_events()}, {"commit"})

    def test_parent_tree_captures_every_repo_with_its_own_label(self) -> None:
        # Run from a NON-repo parent holding two real checkouts: both are
        # discovered and each commit carries its own repo's origin label (#652).
        with tempfile.TemporaryDirectory() as root:
            for name in ("one", "two"):
                repo = Path(root) / "nested" / name
                repo.mkdir(parents=True)
                self._init_repo(repo, origin=f"git@github.com:acme/{name}.git")
                (repo / "f.txt").write_text(name)
                self._git("add", "-A", cwd=repo)
                self._git("commit", "--no-verify", "-qm", f"{name} (task 24648)", cwd=repo)

            # resync_authors avoids depending on the host's global user.email
            # (the parent dir is not a repo, so repo-local config cannot apply).
            result, state = self._run_in(root, _config(resync_authors="dev@example.com"))

        self.assertEqual(result["repos"], 2)
        self.assertEqual(result["inserted"], 2)
        self.assertEqual(result["found"], 2)
        self.assertEqual(
            {e.repo for e in state.get_events()}, {"acme/one", "acme/two"}
        )


class TestSyncGithub(unittest.TestCase):
    # ``gh search prs`` items: NO mergedAt/headRefName (the real search JSON
    # lacks both — the #653 root cause); those come from the per-PR detail.
    _PRS = (
        '[{"number": 7, "title": "PR (task 24648)", "state": "merged",'
        ' "createdAt": "2026-07-01T09:00:00Z", "updatedAt": "2026-07-02T09:00:00Z",'
        ' "repository": {"nameWithOwner": "o/r"}},'
        ' {"number": 8, "title": "Open PR (task 55555)", "state": "open",'
        ' "createdAt": "2026-07-03T09:00:00Z", "updatedAt": "2026-07-03T09:00:00Z",'
        ' "repository": {"nameWithOwner": "o/r"}}]'
    )
    _DETAIL_7 = '{"headRefName": "24648-feat", "mergedAt": "2026-07-02T09:00:00Z"}'
    _DETAIL_8 = '{"headRefName": "55555-wip", "mergedAt": null}'
    _OWN_REVIEWS = (
        '[{"id": 55, "user": {"login": "octocat"}, "submitted_at": "2026-07-02T10:00:00Z"},'
        ' {"id": 56, "user": {"login": "someone-else"}, "submitted_at": "2026-07-02T11:00:00Z"}]'
    )
    _REVIEWED_PRS = (
        '[{"number": 3, "title": "Others PR (task 33333)",'
        ' "repository": {"nameWithOwner": "other/repo"}}]'
    )
    _DETAIL_3 = '{"headRefName": "33333-things", "mergedAt": null}'
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
            (_has("search", "prs", "--author"), self._PRS),
            (_pr_view(7, "o/r"), self._DETAIL_7),
            (_pr_view(8, "o/r"), self._DETAIL_8),
            (lambda c: c[-1] == "repos/o/r/pulls/7/reviews", self._OWN_REVIEWS),
            (lambda c: c[-1] == "repos/o/r/pulls/8/reviews", "[]"),
            (_has("search", "prs", "--reviewed-by"), self._REVIEWED_PRS),
            (_pr_view(3, "other/repo"), self._DETAIL_3),
            (lambda c: c[-1] == "repos/other/repo/pulls/3/reviews", self._OTHER_REVIEWS),
            (_has("search", "issues"), self._COMMENTED),
            (lambda c: c[-1] == "repos/other/repo/issues/9/comments", self._COMMENTS),
        ]

    def test_captures_prs_reviews_and_comments(self) -> None:
        state = _tmp_state()
        with patch.object(ex.subprocess, "run", _fake_run(self._routes())):
            result = ex.sync_github(state, _config(), now=_NOW)
        # opened(7) + merge(7) + opened(8) + own review(55) + others review(77)
        # + comment(111). PR 8 is unmerged, so it mints NO merge event (#656).
        self.assertEqual(result, {"inserted": 6, "found": 6})
        by_ext = {e.external_id: e for e in state.get_events()}
        # PR ids are repo-qualified (#652): account-wide numbers collide.
        self.assertEqual(set(by_ext), {
            "gh:pr:o/r:7:opened", "gh:pr:o/r:7", "gh:pr:o/r:8:opened",
            "gh:review:55", "gh:review:77", "gh:comment:111",
        })
        # A merged PR yields BOTH a billable pr_opened event at createdAt and an
        # audit-only merge event at the detail-fetched mergedAt (#656).
        opened = by_ext["gh:pr:o/r:7:opened"]
        self.assertEqual(opened.source, "pr_opened")
        self.assertEqual(opened.timestamp, datetime(2026, 7, 1, 9, tzinfo=timezone.utc))
        merged = by_ext["gh:pr:o/r:7"]
        self.assertEqual(merged.source, "merge")
        self.assertEqual(merged.timestamp, datetime(2026, 7, 2, 9, tzinfo=timezone.utc))
        self.assertEqual(merged.branch, "24648-feat")
        self.assertEqual(merged.repo, "o/r")
        # Unmerged PR mints only its pr_opened event, timestamped at createdAt.
        self.assertEqual(by_ext["gh:pr:o/r:8:opened"].source, "pr_opened")
        self.assertEqual(by_ext["gh:pr:o/r:8:opened"].task_ids, ["55555"])
        # Own review reads the enriched parent's branch (#653).
        self.assertEqual(by_ext["gh:review:55"].branch, "24648-feat")
        self.assertEqual(by_ext["gh:review:55"].task_ids, ["24648"])
        # Comment is attributed via the issue title, on its own repo.
        comment = by_ext["gh:comment:111"]
        self.assertEqual(comment.source, "comment")
        self.assertEqual(comment.repo, "other/repo")
        self.assertEqual(comment.task_ids, ["44444"])
        # Review on someone else's PR is attributed and stored against that repo.
        self.assertEqual(by_ext["gh:review:77"].repo, "other/repo")
        # A review by a different user on our PR is never attributed to us.
        self.assertNotIn("gh:review:56", by_ext)

    def test_search_commands_are_account_wide_and_date_bounded(self) -> None:
        captured = []

        def _capture(cmd):
            captured.append(cmd)
            return "octocat" if cmd[:3] == ["gh", "api", "user"] else "[]"

        with patch.object(ex, "_run_capture", _capture):
            ex.sync_github(_tmp_state(), _config(resync_window_days=14), now=_NOW)
        authored = next(c for c in captured if "--author" in c)
        # Account-wide search (no repo scope), server-side --updated bound: a
        # PR created before the window but merged inside it is still returned
        # (a --created range would silently lose it).
        self.assertEqual(authored[:3], ["gh", "search", "prs"])
        self.assertIn("--updated", authored)
        self.assertIn(">=2026-07-01", authored)
        self.assertNotIn("--created", authored)
        reviewed = next(c for c in captured if "--reviewed-by" in c)
        self.assertIn("--updated", reviewed)
        self.assertIn(">=2026-07-01", reviewed)
        commented = next(c for c in captured if "--commenter" in c)
        self.assertIn(">=2026-07-01", commented)

    def test_out_of_window_events_skipped(self) -> None:
        state = _tmp_state()
        with patch.object(ex.subprocess, "run", _fake_run(self._routes())):
            # A 3-day window ends before every July artifact here.
            result = ex.sync_github(state, _config(resync_window_days=3), now=_NOW)
        self.assertEqual(result, {"inserted": 0, "found": 0})

    def test_review_on_id_less_title_attributes_via_fetched_branch(self) -> None:
        # #653 regression: the reviewed-by search JSON has no headRefName, so
        # a review whose PR title carries no id NEVER attributed. The per-PR
        # detail fetch restores the branch and the branch-encoded id fires.
        state = _tmp_state()
        reviewed = (
            '[{"number": 12, "title": "Fix payment token on invoice",'
            ' "repository": {"nameWithOwner": "other/repo"}}]'
        )
        detail = '{"headRefName": "24648-payment-token-invoice", "mergedAt": null}'
        reviews = (
            '[{"id": 88, "user": {"login": "octocat"},'
            ' "submitted_at": "2026-07-05T12:00:00Z"}]'
        )
        routes = [
            (_has("api", "user"), "octocat"),
            (_has("search", "prs", "--author"), "[]"),
            (_has("search", "prs", "--reviewed-by"), reviewed),
            (_pr_view(12, "other/repo"), detail),
            (lambda c: c[-1] == "repos/other/repo/pulls/12/reviews", reviews),
            (_has("search", "issues"), "[]"),
        ]
        with patch.object(ex.subprocess, "run", _fake_run(routes)):
            result = ex.sync_github(state, _config(), now=_NOW)
        self.assertEqual(result, {"inserted": 1, "found": 1})
        event = state.get_events()[0]
        self.assertEqual(event.source, "review")
        self.assertEqual(event.branch, "24648-payment-token-invoice")
        self.assertEqual(event.task_ids, ["24648"])

    def test_zero_id_review_is_reported_unattributed(self) -> None:
        # #653 second ask: a review that resolved no task id still stores (for
        # triage) but is surfaced in the summary instead of silently unbillable.
        state = _tmp_state()
        reviewed = (
            '[{"number": 5, "title": "Misc cleanup",'
            ' "repository": {"nameWithOwner": "other/repo"}}]'
        )
        detail = '{"headRefName": "cleanup-pass", "mergedAt": null}'
        reviews = (
            '[{"id": 91, "user": {"login": "octocat"},'
            ' "submitted_at": "2026-07-05T12:00:00Z"}]'
        )
        routes = [
            (_has("api", "user"), "octocat"),
            (_has("search", "prs", "--author"), "[]"),
            (_has("search", "prs", "--reviewed-by"), reviewed),
            (_pr_view(5, "other/repo"), detail),
            (lambda c: c[-1] == "repos/other/repo/pulls/5/reviews", reviews),
            (_has("search", "issues"), "[]"),
        ]
        with patch.object(ex.subprocess, "run", _fake_run(routes)):
            result = ex.sync_github(state, _config(), now=_NOW)
            second = ex.sync_github(state, _config(), now=_NOW)
        self.assertEqual(
            result,
            {"inserted": 1, "found": 1, "unattributed_reviews": ["other/repo#5"]},
        )
        # The warning is first-sight only: the stored review may be triaged
        # later, so a re-run must not cry wolf about it forever.
        self.assertEqual(second, {"inserted": 0, "found": 1})

    def test_detail_fetch_failure_still_mints_branchless_event(self) -> None:
        # The per-PR detail degrades to {} (branch-less event), replacing the
        # old slug-unresolved no-op: one unreadable PR never drops the capture.
        state = _tmp_state()
        prs = (
            '[{"number": 7, "title": "PR (task 24648)", "state": "open",'
            ' "createdAt": "2026-07-01T09:00:00Z",'
            ' "repository": {"nameWithOwner": "o/r"}}]'
        )
        routes = [
            (_has("api", "user"), "octocat"),
            (_has("search", "prs", "--author"), prs),
            (_pr_view(7, "o/r"), None),  # detail fetch fails
            (lambda c: c[-1] == "repos/o/r/pulls/7/reviews", "[]"),
            (_has("search", "prs", "--reviewed-by"), "[]"),
            (_has("search", "issues"), "[]"),
        ]
        with patch.object(ex.subprocess, "run", _fake_run(routes)):
            result = ex.sync_github(state, _config(), now=_NOW)
        # The PR is unmerged (and its mergedAt unreadable), so only the
        # billable pr_opened event mints (#656).
        self.assertEqual(result, {"inserted": 1, "found": 1})
        event = state.get_events()[0]
        self.assertEqual(event.external_id, "gh:pr:o/r:7:opened")
        self.assertEqual(event.branch, "")  # degraded, not dropped
        self.assertEqual(event.task_ids, ["24648"])  # title still attributes

    def test_bare_leading_id_title_attributes_the_billable_opened_event(self) -> None:
        # Regression for the #669<->#672 interaction gap: #672 threaded
        # ``allow_leading_id`` through every builder present in its base, but
        # ``_pr_opened_event`` arrived independently via #669 and was missed.
        # ``pr_opened`` is the BILLABLE review-family source while ``merge`` is
        # audit-only, so a bare-leading-id title (#654) billed NOTHING while its
        # audit row attributed fine. The branch here carries no id, so only the
        # leading form can attribute — isolating the gate.
        state = _tmp_state()
        prs = (
            '[{"number": 21, "title": "24648 rtv process", "state": "merged",'
            ' "createdAt": "2026-07-01T09:00:00Z",'
            ' "updatedAt": "2026-07-02T09:00:00Z",'
            ' "repository": {"nameWithOwner": "o/r"}}]'
        )
        detail = '{"headRefName": "rtv-process", "mergedAt": "2026-07-02T09:00:00Z"}'
        routes = [
            (_has("api", "user"), "octocat"),
            (_has("search", "prs", "--author"), prs),
            (_pr_view(21, "o/r"), detail),
            (lambda c: c[-1] == "repos/o/r/pulls/21/reviews", "[]"),
            (_has("search", "prs", "--reviewed-by"), "[]"),
            (_has("search", "issues"), "[]"),
        ]
        with patch.object(ex.subprocess, "run", _fake_run(routes)):
            ex.sync_github(state, _config(), now=_NOW)
        by_ext = {e.external_id: e for e in state.get_events()}
        opened = by_ext["gh:pr:o/r:21:opened"]
        self.assertEqual(opened.source, "pr_opened")
        self.assertEqual(opened.task_ids, ["24648"])  # was [] — the billing leak
        # The audit-only merge row always attributed; that asymmetry WAS the bug.
        self.assertEqual(by_ext["gh:pr:o/r:21"].task_ids, ["24648"])

    def test_self_review_on_own_pr_collected_once(self) -> None:
        # The reviewed-by search has no author filter, so an own PR can come
        # back from it too; it must be excluded there (it is already covered by
        # the authored path) or one self-review would be double-collected.
        state = _tmp_state()
        own_pr = (
            '[{"number": 7, "title": "PR (task 24648)", "state": "open",'
            ' "createdAt": "2026-07-01T09:00:00Z",'
            ' "repository": {"nameWithOwner": "o/r"}}]'
        )
        routes = [
            (_has("api", "user"), "octocat"),
            (_has("search", "prs", "--author"), own_pr),
            (_pr_view(7, "o/r"), self._DETAIL_7),
            (lambda c: c[-1] == "repos/o/r/pulls/7/reviews", self._OWN_REVIEWS),
            (_has("search", "prs", "--reviewed-by"), own_pr),  # same PR again
            (_has("search", "issues"), "[]"),
        ]
        with patch.object(ex.subprocess, "run", _fake_run(routes)):
            result = ex.sync_github(state, _config(), now=_NOW)
        # opened(7) + merge(7) + ONE review(55) — not two copies of the
        # self-review (the authored PR itself mints both #656 events).
        self.assertEqual(result, {"inserted": 3, "found": 3})

    def test_search_hitting_its_cap_warns(self) -> None:
        # A result exactly at the fixed --limit is very likely clipped; found
        # must not masquerade as complete coverage.
        state = _tmp_state()
        prs = json.dumps(
            [
                {
                    "number": n,
                    "title": f"PR {n} (task 24648)",
                    "state": "open",
                    "createdAt": "2026-07-03T09:00:00Z",
                    "repository": {"nameWithOwner": "o/r"},
                }
                for n in range(1, ex._AUTHORED_SEARCH_LIMIT + 1)
            ]
        )
        routes = [
            (_has("api", "user"), "octocat"),
            (_has("search", "prs", "--author"), prs),
            (_has("pr", "view"), '{"headRefName": "", "mergedAt": null}'),
            (lambda c: c[-1].endswith("/reviews"), "[]"),
            (_has("search", "prs", "--reviewed-by"), "[]"),
            (_has("search", "issues"), "[]"),
        ]
        with patch.object(ex.subprocess, "run", _fake_run(routes)):
            result = ex.sync_github(state, _config(), now=_NOW)
        self.assertEqual(result["found"], ex._AUTHORED_SEARCH_LIMIT)
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("authored-PR (octocat)", result["warnings"][0])
        self.assertIn("truncated", result["warnings"][0])

    def test_review_family_fetch_paginates(self) -> None:
        # Without --paginate only the oldest 30 reviews would ever be read.
        captured = []

        def fake_gh_json(cmd):
            captured.append(cmd)
            return []

        window = ex._Window(datetime(2026, 7, 1, tzinfo=timezone.utc), _NOW)
        with patch.object(ex, "_gh_json", fake_gh_json):
            ex._collect_review_family(
                [({"number": 1}, "o/r", "o/r")],
                "octocat",
                window,
                ex._reviews_path,
                ex._review_event,
            )
        self.assertEqual(
            captured[0], ["gh", "api", "--paginate", "repos/o/r/pulls/1/reviews"]
        )

    def test_inverted_window_is_error(self) -> None:
        # An --end older than the rolling window's start must fail loudly, not
        # silently sweep nothing (#652 error contract).
        state = _tmp_state()
        routes = [(_has("api", "user"), "octocat")]
        with patch.object(ex.subprocess, "run", _fake_run(routes)):
            result = ex.sync_github(state, _config(), now=_NOW, end=date(2026, 1, 31))
        self.assertIn("empty resync window", result["error"])

    def test_two_identities_both_captured(self) -> None:
        state = _tmp_state()
        prs_for = {
            "octo-a": '[{"number": 1, "title": "A (task 24648)", "state": "merged",'
            ' "createdAt": "2026-07-01T09:00:00Z",'
            ' "repository": {"nameWithOwner": "o/r"}}]',
            "octo-b": '[{"number": 2, "title": "B (task 55555)", "state": "merged",'
            ' "createdAt": "2026-07-01T09:00:00Z",'
            ' "repository": {"nameWithOwner": "o/r"}}]',
        }
        routes = [
            (_has("api", "user"), "octo-a"),
            (lambda c: "--author" in c and "octo-a" in c, prs_for["octo-a"]),
            (lambda c: "--author" in c and "octo-b" in c, prs_for["octo-b"]),
            (_pr_view(1, "o/r"), '{"headRefName": "24648-a", "mergedAt": "2026-07-02T09:00:00Z"}'),
            (_pr_view(2, "o/r"), '{"headRefName": "55555-b", "mergedAt": "2026-07-02T09:00:00Z"}'),
            (lambda c: c[-1].endswith("/reviews"), "[]"),
            (_has("search", "prs", "--reviewed-by"), "[]"),
            (_has("search", "issues"), "[]"),
        ]
        cfg = _config(resync_authors="octo-a octo-b")
        with patch.object(ex.subprocess, "run", _fake_run(routes)):
            result = ex.sync_github(state, cfg, now=_NOW)
        # Each identity's merged PR mints an opened AND a merge event (#656),
        # both under repo-qualified ids (#652).
        self.assertEqual(result, {"inserted": 4, "found": 4})
        self.assertEqual(
            {e.external_id for e in state.get_events()},
            {
                "gh:pr:o/r:1:opened", "gh:pr:o/r:1",
                "gh:pr:o/r:2:opened", "gh:pr:o/r:2",
            },
        )

    def test_search_results_without_repository_are_skipped(self) -> None:
        state = _tmp_state()
        routes = [
            (_has("api", "user"), "octocat"),
            (
                _has("search", "prs", "--author"),
                '[{"number": 3, "title": "no repo", "createdAt": "2026-07-03T09:00:00Z"}]',
            ),
            (_has("search", "prs", "--reviewed-by"), '[{"number": 3, "title": "no repo"}]'),
            (_has("search", "issues"), '[{"number": 9, "title": "no repo"}]'),
        ]
        with patch.object(ex.subprocess, "run", _fake_run(routes)):
            result = ex.sync_github(state, _config(), now=_NOW)
        self.assertEqual(result, {"inserted": 0, "found": 0})

    def test_second_run_is_idempotent(self) -> None:
        state = _tmp_state()
        with patch.object(ex.subprocess, "run", _fake_run(self._routes())):
            ex.sync_github(state, _config(), now=_NOW)
            second = ex.sync_github(state, _config(), now=_NOW)
        self.assertEqual(second, {"inserted": 0, "found": 6})

    def test_gh_missing_is_error(self) -> None:
        state = _tmp_state()
        with patch.object(ex.subprocess, "run", _fake_run([], missing=("gh",))):
            result = ex.sync_github(state, _config(), now=_NOW)
        self.assertEqual(result, {"error": "gh unavailable or not authenticated"})

    def test_authored_search_failure_is_error(self) -> None:
        state = _tmp_state()
        routes = [
            (_has("api", "user"), "octocat"),
            (_has("search", "prs", "--author"), None),
        ]
        with patch.object(ex.subprocess, "run", _fake_run(routes)):
            result = ex.sync_github(state, _config(), now=_NOW)
        self.assertEqual(result, {"error": "gh search prs failed"})


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
        until = datetime(2026, 7, 10, tzinfo=timezone.utc)
        self.assertEqual(
            ex._ts_in_window("2026-07-02T00:00:00Z", since, until),
            datetime(2026, 7, 2, tzinfo=timezone.utc),
        )
        # The lower bound is inclusive; the upper bound is exclusive.
        self.assertEqual(ex._ts_in_window("2026-07-01T00:00:00Z", since, until), since)
        self.assertIsNone(ex._ts_in_window("2026-07-10T00:00:00Z", since, until))
        self.assertIsNone(ex._ts_in_window("2026-07-11T00:00:00Z", since, until))
        self.assertIsNone(ex._ts_in_window("2026-06-30T00:00:00Z", since, until))
        self.assertIsNone(ex._ts_in_window(None, since, until))
        self.assertIsNone(ex._ts_in_window("not-a-date", since, until))

    def test_window_bounds_defaults_and_explicit_range(self) -> None:
        cases = [
            # (start, end, expected_since, expected_until)
            (None, None, datetime(2026, 6, 15, tzinfo=timezone.utc), _NOW),
            (date(2026, 7, 1), None, datetime(2026, 7, 1, tzinfo=timezone.utc), _NOW),
            (
                None,
                date(2026, 7, 10),
                datetime(2026, 6, 15, tzinfo=timezone.utc),
                datetime(2026, 7, 11, tzinfo=timezone.utc),  # inclusive end: +1 day
            ),
            (
                date(2026, 7, 1),
                date(2026, 7, 1),
                datetime(2026, 7, 1, tzinfo=timezone.utc),
                datetime(2026, 7, 2, tzinfo=timezone.utc),  # one whole day
            ),
        ]
        config = _config(resync_window_days=30)
        for start, end, since, until in cases:
            with self.subTest(start=start, end=end):
                self.assertEqual(
                    ex._window_bounds(config, _NOW, start, end), (since, until)
                )

    def test_window_bounds_rejects_empty_or_inverted_ranges(self) -> None:
        config = _config(resync_window_days=30)
        cases = [
            (date(2026, 8, 1), date(2026, 7, 1)),  # start after end
            (None, date(2026, 1, 31)),  # end before the rolling window start
        ]
        for start, end in cases:
            with self.subTest(start=start, end=end):
                with self.assertRaises(ValueError):
                    ex._window_bounds(config, _NOW, start, end)

    def test_gh_json_returns_none_on_bad_json(self) -> None:
        with patch.object(ex.subprocess, "run", _fake_run([(_has("api"), "not json")])):
            self.assertIsNone(ex._gh_json(["gh", "api", "x"]))

    def test_pr_opened_event_skips_without_created_at_or_repo(self) -> None:
        window = ex._Window(datetime(2026, 1, 1, tzinfo=timezone.utc), _NOW)
        with_repo = {
            "number": 1,
            "repository": {"nameWithOwner": "o/r"},
            "createdAt": None,
        }
        self.assertIsNone(ex._pr_opened_event(with_repo, window))
        no_repo = {"number": 1, "createdAt": "2026-07-01T09:00:00Z"}
        self.assertIsNone(ex._pr_opened_event(no_repo, window))

    def test_pr_merged_event_skips_unmerged_pr_or_repoless_item(self) -> None:
        # An unmerged PR mints NO merge event — createdAt is no fallback (#656);
        # the creation is captured by _pr_opened_event instead.
        window = ex._Window(datetime(2026, 1, 1, tzinfo=timezone.utc), _NOW)
        with_repo = {
            "number": 1,
            "repository": {"nameWithOwner": "o/r"},
            "mergedAt": None,
            "createdAt": "2026-07-01T09:00:00Z",
        }
        self.assertIsNone(ex._pr_merged_event(with_repo, window))
        no_repo = {"number": 1, "mergedAt": "2026-07-02T09:00:00Z"}
        self.assertIsNone(ex._pr_merged_event(no_repo, window))

    def test_pr_created_in_window_but_merged_after_still_mints(self) -> None:
        # An explicit backfill range: created inside it, merged after it — the
        # PR must still be captured (not vanish entirely). Since #656 split the
        # combined event, the creation is carried by the pr_opened event rather
        # than by a createdAt fallback on the merge event.
        window = ex._Window(
            datetime(2026, 7, 1, tzinfo=timezone.utc),
            datetime(2026, 8, 1, tzinfo=timezone.utc),
        )
        pr = {
            "number": 4,
            "title": "t (task 24648)",
            "repository": {"nameWithOwner": "o/r"},
            "createdAt": "2026-07-20T09:00:00Z",
            "mergedAt": "2026-08-03T09:00:00Z",  # outside the window
        }
        opened = ex._pr_opened_event(pr, window)
        self.assertIsNotNone(opened)
        self.assertEqual(
            opened.timestamp, datetime(2026, 7, 20, 9, tzinfo=timezone.utc)
        )
        self.assertEqual(opened.external_id, "gh:pr:o/r:4:opened")
        # The out-of-window merge itself is not backdated into the range.
        self.assertIsNone(ex._pr_merged_event(pr, window))

    def test_review_event_skips_without_submitted_at(self) -> None:
        pr = {"number": 1, "title": "t", "headRefName": "b"}
        review = {"id": 9, "submitted_at": None}
        window = ex._Window(datetime(2026, 1, 1, tzinfo=timezone.utc), _NOW)
        self.assertIsNone(ex._review_event(review, pr, "o/r", window))

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
