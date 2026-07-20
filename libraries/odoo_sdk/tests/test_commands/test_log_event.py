"""Tests for :class:`LogEventCommand`, the command-layer owner of the events
append write (issue #407).

The command is the single place an ``EventRecord`` is constructed and handed to
:meth:`LocalStateClient.add_event`; the CLI ``log-event`` subcommand and the MCP
dispatch-event wrapper both route their writes through it. These tests drive the
command directly against a schema-provisioned temp DB and assert the persisted
row, so the shared write contract is pinned in one place.
"""

import os
import sqlite3
import subprocess
import unittest
from datetime import datetime, timedelta, timezone
from typing import Optional
from unittest.mock import Mock, patch

from odoo_sdk.commands import LogEventCommand
from odoo_sdk.commands.builtin import BUILTIN_COMMANDS
from odoo_sdk.commands.log_event import (
    current_branch_label,
    normalize_task_ids,
)
from odoo_sdk.reap import REAP_THRESHOLD_ENV
from tests.support import make_state_db, make_state_db_path

#: Patch targets for the two best-effort git lookups the command performs. Every
#: test that is not specifically about provenance stubs both, so the suite never
#: depends on the ambient checkout it happens to run in.
REPO_LABEL = "odoo_sdk.commands.log_event.current_repo_label"
BRANCH_LABEL = "odoo_sdk.commands.log_event.current_branch_label"


class TestLogEventCommand(unittest.TestCase):
    def setUp(self) -> None:
        self.db = make_state_db()
        for target in (REPO_LABEL, BRANCH_LABEL):
            patcher = patch(target, return_value="")
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_not_a_builtin_command(self) -> None:
        # Event emission must never be an LLM-callable tool, and the built-in
        # surface is a bijection with the MCP tool surface, so ``log_event`` is
        # deliberately NOT registered as a built-in.
        self.assertNotIn("log_event", BUILTIN_COMMANDS)

    def test_writes_all_fields_verbatim(self) -> None:
        when = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        result = LogEventCommand(state=self.db).execute(
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
        LogEventCommand(state=self.db).execute(source="agent")
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
        before = datetime.now(timezone.utc)
        LogEventCommand(state=self.db).execute(source="agent", timestamp=None)
        after = datetime.now(timezone.utc)
        stamped = self.db.get_events()[0].timestamp
        self.assertGreaterEqual(stamped, before)
        self.assertLessEqual(stamped, after)

    def test_task_ids_are_copied_not_aliased(self) -> None:
        # A caller mutating its list after the call must not affect the row.
        scope = ["5"]
        LogEventCommand(state=self.db).execute(source="agent", task_ids=scope)
        scope.append("6")
        self.assertEqual(self.db.get_events()[0].task_ids, ["5"])

    def test_client_is_optional_and_unused(self) -> None:
        # The command performs no Odoo RPC: constructing it with no client must
        # work, and no method may be called on an injected client.
        client = Mock()
        LogEventCommand(client=client, state=self.db).execute(source="agent")
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


class TestCurrentBranchLabel(unittest.TestCase):
    """Branch resolution is best-effort: it never raises on the write path."""

    def _git(self, stdout: str = "", exc: Optional[Exception] = None):
        if exc is not None:
            return patch("odoo_sdk.commands.log_event.subprocess.run", side_effect=exc)
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout)
        return patch(
            "odoo_sdk.commands.log_event.subprocess.run", return_value=completed
        )

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
    written while a run is active attributes to that run.
    """

    def setUp(self) -> None:
        self.db_path = make_state_db_path()
        self.db = make_state_db(self.db_path)
        self.command = LogEventCommand(state=self.db)
        for target in (REPO_LABEL, BRANCH_LABEL):
            patcher = patch(target, return_value="")
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(os.environ.pop, REAP_THRESHOLD_ENV, None)

    def _backdate(self, run_id: int, hours: float) -> None:
        """Backdate one run's ``started_at`` so it reads as stale."""
        old = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("UPDATE task_runs SET started_at = ? WHERE id = ?", (old, run_id))
        conn.commit()
        conn.close()

    def test_explicit_hint_wins_over_active_runs(self) -> None:
        self.db.create_run(101, "Task A", 1, "Proj")
        self.assertEqual(self.command.resolve_task_ids([999]), ["999"])

    def test_unhinted_attributes_to_every_active_run(self) -> None:
        self.db.create_run(101, "Task A", 1, "Proj")
        self.db.create_run(202, "Task B", 1, "Proj")
        self.assertEqual(sorted(self.command.resolve_task_ids()), ["101", "202"])

    def test_unhinted_with_no_active_run_is_untargeted(self) -> None:
        self.assertEqual(self.command.resolve_task_ids(), [])

    def test_attach_active_run_disabled_stays_untargeted(self) -> None:
        self.db.create_run(101, "Task A", 1, "Proj")
        self.assertEqual(self.command.resolve_task_ids(attach_active_run=False), [])

    def test_uncoercible_hint_falls_back_to_active_runs(self) -> None:
        # The MCP wrapper hands over whatever the bound ``task_id`` held. A
        # value that names no task leaves the event unhinted, not unattributed.
        self.db.create_run(101, "Task A", 1, "Proj")
        self.assertEqual(self.command.resolve_task_ids(["abc"]), ["101"])

    def test_stale_run_is_excluded(self) -> None:
        self.db.create_run(101, "Fresh", 1, "Proj")
        stale = self.db.create_run(202, "Wedged", 1, "Proj")
        self._backdate(stale.id, 30)
        self.assertEqual(self.command.resolve_task_ids(), ["101"])

    def test_env_override_widens_staleness_threshold(self) -> None:
        stale = self.db.create_run(202, "Wedged", 1, "Proj")
        self._backdate(stale.id, 30)
        os.environ[REAP_THRESHOLD_ENV] = "48"
        self.assertEqual(self.command.resolve_task_ids(), ["202"])

    def test_execute_persists_the_active_run_fallback(self) -> None:
        # The end-to-end shape the MCP dispatch path now takes: a tool call with
        # no ``task_id`` still lands a billable, sessionizable row.
        self.db.create_run(101, "Task A", 1, "Proj")
        result = self.command.execute(source="agent", subject="get_task")
        self.assertEqual(self.db.get_events()[0].task_ids, ["101"])
        self.assertEqual(result["task_ids"], ["101"])

    def test_execute_normalizes_the_explicit_hint(self) -> None:
        self.command.execute(source="agent", task_ids=[42])
        self.assertEqual(self.db.get_events()[0].task_ids, ["42"])


class TestProvenanceResolution(unittest.TestCase):
    """``repo`` / ``branch`` are resolved by the command, not by the caller (#509)."""

    def setUp(self) -> None:
        self.db = make_state_db()

    def _execute(self, **kwargs) -> None:
        with (
            patch(REPO_LABEL, return_value="o/r") as repo_label,
            patch(BRANCH_LABEL, return_value="feat/kiosk") as branch_label,
        ):
            LogEventCommand(state=self.db).execute(source="agent", **kwargs)
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
