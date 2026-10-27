"""Tests for the ``log_event`` builtin, the command-layer owner of the events
append write (issue #407).

The command is the single place an ``EventRecord`` is constructed and handed to
:meth:`EventStore.add_event`; the CLI ``log-event`` subcommand and the MCP
dispatch-event wrapper both route their writes through it. These tests drive the
command directly against a schema-provisioned temp DB and assert the persisted
row, so the shared write contract is pinned in one place.
"""

import subprocess
import unittest
from datetime import datetime, timezone
from typing import Optional
from unittest.mock import Mock, patch

from odoo_dev_core.attribution import (
    _CONVENTIONAL_BRANCH_TYPES,
    current_branch_label,
    normalize_task_ids,
    resolve_task_ids,
    task_ids_from_branch,
)
from ...support import make_event_store
from ...fakes import FakeGit, FixedClock, service
from odoo_dev_core import default_registry

#: Patch target for the best-effort branch lookup the command performs. The repo
#: lookup goes through ``FakeGit.current_repo_label`` (``""``), so the suite never
#: depends on the ambient checkout it happens to run in.
BRANCH_LABEL = "odoo_dev_core.builtins.log_event.current_branch_label"


class TestLogEventCommand(unittest.TestCase):
    def setUp(self) -> None:
        self.db = make_event_store()
        for target in (BRANCH_LABEL,):
            patcher = patch(target, return_value="")
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_is_a_cli_only_command(self) -> None:
        # Event emission must never be an LLM-callable tool, so ``log_event`` is
        # registered for the CLI surface only.
        spec = default_registry().get("log_event")
        self.assertEqual(spec.surfaces, frozenset({"cli"}))

    def test_writes_all_fields_verbatim(self) -> None:
        when = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        result = service(events=self.db).execute(
            "log_event",
            source="claude:PostToolUse",
            subject="work",
            payload={"tool": "Bash"},
            task_ids=["101", "202"],
            repo="o/r",
            branch="feat/kiosk",
            pr_num=202,
            external_id="git:abc123",
            timestamp=when,
        )
        events = self.db.get_events()
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.source, "claude:PostToolUse")
        self.assertEqual(event.subject, "work")
        self.assertEqual(event.payload, {"tool": "Bash"})
        self.assertEqual(event.task_ids, ["101", "202"])
        self.assertEqual(event.repo, "o/r")
        self.assertEqual(event.branch, "feat/kiosk")
        self.assertEqual(event.pr_num, 202)
        self.assertEqual(event.external_id, "git:abc123")
        self.assertEqual(event.timestamp, when)
        # The return value summarizes the written row for the caller.
        self.assertEqual(
            result,
            {
                "source": "claude:PostToolUse",
                "subject": "work",
                "task_ids": ["101", "202"],
            },
        )

    def test_defaults_are_untargeted_empty_row(self) -> None:
        # Only ``source`` is required. With no active run to attribute to and
        # both git lookups stubbed empty (see setUp), every other field lands on
        # the untargeted/empty value the frontends rely on.
        service(events=self.db).execute("log_event", source="agent")
        event = self.db.get_events()[0]
        self.assertEqual(event.source, "agent")
        self.assertEqual(event.subject, "")
        self.assertIsNone(event.payload)
        self.assertEqual(event.task_ids, [])
        self.assertEqual(event.repo, "")
        self.assertEqual(event.branch, "")
        self.assertEqual(event.pr_num, 0)
        self.assertIsNone(event.external_id)

    def test_timestamp_defaults_to_now_when_none(self) -> None:
        # The default timestamp is the injected clock's reading.
        now = datetime(2026, 3, 4, 5, 6, 7, tzinfo=timezone.utc)
        service(events=self.db, clock=FixedClock(now)).execute(
            "log_event", source="agent", timestamp=None
        )
        self.assertEqual(self.db.get_events()[0].timestamp, now)

    def test_task_ids_are_copied_not_aliased(self) -> None:
        # A caller mutating its list after the call must not affect the row.
        scope = ["5"]
        service(events=self.db).execute("log_event", source="agent", task_ids=scope)
        scope.append("6")
        self.assertEqual(self.db.get_events()[0].task_ids, ["5"])

    def test_client_is_optional_and_unused(self) -> None:
        # The command performs no Odoo RPC: constructing it with no client must
        # work, and no method may be called on an injected client.
        client = Mock()
        service(client=client, events=self.db).execute("log_event", source="agent")
        client.assert_not_called()
        self.assertEqual(len(self.db.get_events()), 1)


class TestNormalizeTaskIds(unittest.TestCase):
    """The one coercion rule for explicit attribution hints (#507)."""

    def test_int_and_str_converge(self) -> None:
        self.assertEqual(normalize_task_ids([5, "5", " 5 "]), ["5", "5", "5"])

    def test_none_iterable_is_empty(self) -> None:
        self.assertEqual(normalize_task_ids(None), [])

    def test_non_task_values_are_dropped(self) -> None:
        # A free-text value, a ``None`` hole, and a stray object name no task;
        # persisting them would write ids that can never join a real task.
        self.assertEqual(normalize_task_ids(["abc", None, object(), 7]), ["7"])


class TestTaskIdsFromBranch(unittest.TestCase):
    """Recover a task id from the ``<task-id>-<slug>`` session branch (#574, #622)."""

    def test_canonical_branch_yields_its_task_id(self) -> None:
        # The exact form start_task writes: f"{task_id}-{slug}" (#622).
        self.assertEqual(task_ids_from_branch("28788-hook-attribution"), ["28788"])

    def test_slash_prefixed_branch_still_matches(self) -> None:
        # A path-style prefix (feature/28788-slug) is anchored on the '/'.
        self.assertEqual(task_ids_from_branch("feature/28788-slug"), ["28788"])

    def test_none_and_empty_are_empty(self) -> None:
        self.assertEqual(task_ids_from_branch(None), [])
        self.assertEqual(task_ids_from_branch(""), [])

    def test_ordinary_branch_names_have_no_task_id(self) -> None:
        for branch in ("main", "master", "feat/kiosk", "fix/wc1-hook"):
            self.assertEqual(task_ids_from_branch(branch), [], branch)

    def test_conventional_commit_prefix_names_an_issue_not_a_task(self) -> None:
        # #1040: branches like ``docs/1019-shellcheck-form-per-file-set``, in a
        # repo with no Odoo project at all, minted phantom task lanes out of
        # GitHub issue numbers. A conventional-commit type as the first path
        # segment IS the GitHub-issue convention, so the digit run after it is
        # an issue number, not a task id.
        for prefix in sorted(_CONVENTIONAL_BRANCH_TYPES):
            branch = f"{prefix}/1019-shellcheck-form-per-file-set"
            self.assertEqual(task_ids_from_branch(branch), [], branch)

    def test_conventional_prefix_match_is_case_insensitive(self) -> None:
        self.assertEqual(task_ids_from_branch("DOCS/1019-slug"), [])

    def test_feature_prefix_is_not_a_conventional_type(self) -> None:
        # Only the conventional-commit spelling carries the convention: a
        # ``feature/`` branch is still the documented #574 task-branch form.
        self.assertEqual(task_ids_from_branch("feature/28788-slug"), ["28788"])

    def test_root_task_branch_survives_the_prefix_rule(self) -> None:
        # The form start_task actually writes has no path prefix at all (#622).
        self.assertEqual(task_ids_from_branch("28788-hook-attribution"), ["28788"])

    def test_short_number_is_not_a_task_id(self) -> None:
        # Fewer than the shared 4-digit floor: a stray number, not a task id, so
        # a hook event and the commit it accompanies never split lanes (#378).
        self.assertEqual(task_ids_from_branch("123-x"), [])

    def test_mid_token_digit_run_is_not_read_as_an_id(self) -> None:
        # Digits buried mid-token ('fixes-28788') are not anchored at a token
        # start, so they must not attribute.
        self.assertEqual(task_ids_from_branch("fixes-28788-x"), [])

    def test_legacy_hash_convention_is_not_parsed(self) -> None:
        # #622 is a clean cutover: the retired hash-delimited form (task id
        # followed by '#' then the slug) no longer attributes at all.
        self.assertEqual(task_ids_from_branch("28788#legacy-slug"), [])

    def test_zero_padded_id_is_canonicalized(self) -> None:
        self.assertEqual(task_ids_from_branch("0028788-slug"), ["28788"])

    def test_duplicate_ids_are_deduped_first_seen(self) -> None:
        self.assertEqual(task_ids_from_branch("28788-a/28788-b"), ["28788"])

    def test_producer_round_trips_through_the_parser(self) -> None:
        # Producer/parser lockstep (#622): a branch built exactly the way the
        # MCP tool builds it must parse back to the same task id.
        task_id, slug = 28788, "hyphenated-branch-slug"
        self.assertEqual(task_ids_from_branch(f"{task_id}-{slug}"), [str(task_id)])


class TestCurrentBranchLabel(unittest.TestCase):
    """Branch resolution is best-effort: it never raises on the write path."""

    def _git(self, stdout: str = "", exc: Optional[Exception] = None):
        if exc is not None:
            return patch("odoo_dev_core.attribution.subprocess.run", side_effect=exc)
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout)
        return patch("odoo_dev_core.attribution.subprocess.run", return_value=completed)

    def test_branch_name_returned(self) -> None:
        with self._git(stdout="feat/kiosk\n"):
            self.assertEqual(current_branch_label(), "feat/kiosk")

    def test_detached_head_and_non_repo_cwd_are_empty(self) -> None:
        # Both make ``symbolic-ref`` exit non-zero, which ``check=True`` raises
        # and the resolver swallows -- there is no branch to record.
        failure = subprocess.CalledProcessError(128, ["git"])
        with self._git(exc=failure):
            self.assertEqual(current_branch_label(), "")

    def test_missing_git_binary_is_empty(self) -> None:
        with self._git(exc=FileNotFoundError("git")):
            self.assertEqual(current_branch_label(), "")


class TestAttributionPolicy(unittest.TestCase):
    """The consolidated ingest-side attribution rule (#507).

    An event that lands with an empty ``task_ids`` is excluded from session
    derivation permanently and can therefore never bill, so an unhinted event
    recovers its task from the stated ``<task-id>-<slug>`` session branch.
    """

    def setUp(self) -> None:
        self.db = make_event_store()
        self.command = service(events=self.db)
        for target in (BRANCH_LABEL,):
            patcher = patch(target, return_value="")
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_explicit_hint_wins(self) -> None:
        self.assertEqual(resolve_task_ids([999]), ["999"])

    def test_unhinted_with_no_active_run_is_untargeted(self) -> None:
        self.assertEqual(resolve_task_ids(), [])

    def test_uncoercible_hint_leaves_the_event_unhinted(self) -> None:
        self.assertEqual(resolve_task_ids(["abc"]), [])

    def test_attach_active_run_disabled_stays_untargeted(self) -> None:
        self.assertEqual(resolve_task_ids(attach_active_run=False), [])

    def test_branch_recovers_task_id(self) -> None:
        # The #574 fix: a session bound to a task branch attributes to that task,
        # not triage.
        self.assertEqual(resolve_task_ids(branch="28788-hook-fix"), ["28788"])

    def test_explicit_hint_wins_over_branch(self) -> None:
        self.assertEqual(resolve_task_ids([999], branch="28788-hook-fix"), ["999"])

    def test_branch_without_task_id_stays_untargeted(self) -> None:
        self.assertEqual(resolve_task_ids(branch="main"), [])

    def test_attach_disabled_ignores_branch(self) -> None:
        # attach_active_run=False keeps its documented "leave untargeted"
        # contract: the branch-recovery fallback is gated with the active-run one.
        self.assertEqual(
            resolve_task_ids(attach_active_run=False, branch="28788-hook-fix"),
            [],
        )

    def test_execute_recovers_task_from_stated_branch(self) -> None:
        # End to end: the hook vector (a stated task branch) lands
        # a billable, sessionizable row instead of an untargeted triage one.
        result = self.command.execute(
            "log_event",
            source="claude:PostToolUse",
            subject="Bash",
            branch="28788-hook-fix",
        )
        event = self.db.get_events()[0]
        self.assertEqual(event.task_ids, ["28788"])
        # The branch is also recorded verbatim as provenance.
        self.assertEqual(event.branch, "28788-hook-fix")
        self.assertEqual(result["task_ids"], ["28788"])

    def test_execute_does_not_recover_from_cwd_resolved_branch(self) -> None:
        # Provenance is best-effort from the working tree, but attribution is not:
        # a branch the caller did NOT state (only the cwd fallback saw) must not
        # silently re-attribute. Here the patched cwd branch names a task, yet the
        # event stays untargeted because nothing stated it.
        with patch(BRANCH_LABEL, return_value="28788-ambient"):
            self.command.execute("log_event", source="claude:Stop")
        event = self.db.get_events()[0]
        self.assertEqual(event.task_ids, [])
        self.assertEqual(event.branch, "28788-ambient")

    def test_execute_normalizes_the_explicit_hint(self) -> None:
        self.command.execute("log_event", source="agent", task_ids=[42])
        self.assertEqual(self.db.get_events()[0].task_ids, ["42"])


class TestProvenanceResolution(unittest.TestCase):
    """``repo`` / ``branch`` are resolved by the command, not by the caller (#509)."""

    def setUp(self) -> None:
        self.db = make_event_store()

    def _execute(self, **kwargs) -> None:
        with (
            patch.object(
                FakeGit, "current_repo_label", return_value="o/r"
            ) as repo_label,
            patch(BRANCH_LABEL, return_value="feat/kiosk") as branch_label,
        ):
            service(events=self.db).execute("log_event", source="agent", **kwargs)
        self.repo_label, self.branch_label = repo_label, branch_label

    def test_unstated_provenance_is_resolved_from_the_working_tree(self) -> None:
        self._execute()
        event = self.db.get_events()[0]
        self.assertEqual(event.repo, "o/r")
        self.assertEqual(event.branch, "feat/kiosk")

    def test_explicit_empty_string_records_none_without_asking_git(self) -> None:
        # "" is a statement that there is no repo/branch, distinct from the
        # ``None`` default that means "work it out"; it must not shell out.
        self._execute(repo="", branch="")
        event = self.db.get_events()[0]
        self.assertEqual(event.repo, "")
        self.assertEqual(event.branch, "")
        self.repo_label.assert_not_called()
        self.branch_label.assert_not_called()

    def test_explicit_values_override_resolution(self) -> None:
        self._execute(repo="other/repo", branch="main")
        event = self.db.get_events()[0]
        self.assertEqual(event.repo, "other/repo")
        self.assertEqual(event.branch, "main")


if __name__ == "__main__":
    unittest.main()
