"""Cross-boundary contract test: the ``claude-event-hook`` shim ↔ ``log-event``.

The devcontainer feature's ``claude-event-hook`` shim shells out to
``odoo-sdk log-event --source claude:<HookName> --attach-active-run [--payload …]``
and swallows every failure by design (it always exits 0 and forks the SDK call
into a detached background job). So a rename of ``--attach-active-run`` /
``--payload`` or of the ``claude:`` source prefix would pass every existing SDK
test, feature grep, and CI run while silently dropping every event — or every
bill — in production (issue #411). This pins that interface loudly:

* The ``claude:`` prefix is a THREE-WAY string contract: the shim *emits* it, the
  persistence adapter *validates* it (``_CLAUDE_SOURCE_PREFIX``), and the billing
  predicate *keys on* it (``_DEVELOPMENT_SOURCE_PREDICATE``'s ``LIKE 'claude:%'``).
  All three literals must be identical — this mirrors the DDL parity gate in
  ``tests/test_state/test_init_script_parity.py``.
* The exact argv the shim emits must still parse, still attach the active run's
  task id via ``--attach-active-run``, still persist the ``--payload`` JSON, and
  still land a row that MATCHES the billing predicate.

This is deliberately a pure/argv-level test (no bash subprocess): it needs no jq
and no installed shim binary, so it runs anywhere the SDK suite runs. The feature
harness (``devcontainer-features/test/personal-features``) covers the full shim →
real-CLI → central-DB subprocess vector on top of this.
"""

import os
import re
import shutil
import sqlite3
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import odoo_sdk.cli.__main__ as cli
from odoo_sdk.adapters.state_persistence import _CLAUDE_SOURCE_PREFIX
from odoo_sdk.state import ATTACHED_TASK_IDS_PAYLOAD_KEY
from odoo_sdk.state import LocalStateClient as TaskStateDB
from odoo_sdk.state.db import _DEVELOPMENT_SOURCE_PREDICATE, tracker_db_path
from odoo_sdk.state.summary import _CWD_KEY, _PROMPT_KEY, summarize_session_context
from tests.support import provision_schema

# Repo root is four parents up from tests/test_cli/<this file> (mirrors the path
# math in test_init_script_parity.py, which reaches sibling top-level dirs).
_REPO_ROOT = Path(__file__).resolve().parents[4]
SHIM = (
    _REPO_ROOT
    / "devcontainer-features"
    / "src"
    / "personal-features"
    / "claude-event-hook"
)


def _shim_text() -> str:
    return SHIM.read_text(encoding="utf-8")


def _shim_source_prefix(text: str) -> str:
    """Extract the literal the shim splices before ``$HOOK_NAME`` in ``--source``.

    Matches ``--source "claude:$HOOK_NAME"`` and returns ``claude:`` — everything
    inside the quotes up to (but excluding) the first ``$`` or closing quote.
    """
    match = re.search(r'--source\s+"([^"$]*)', text)
    assert match is not None, f'no --source "..." argument found in {SHIM}'
    return match.group(1)


def _predicate_claude_prefix(predicate: str) -> str:
    """Extract the ``claude:`` literal from the ``LIKE 'claude:%'`` clause."""
    match = re.search(r"LIKE '([^%']*)%'", predicate)
    assert match is not None, f"no LIKE 'claude:%' clause in predicate: {predicate}"
    return match.group(1)


class TestClaudePrefixThreeWayParity(unittest.TestCase):
    """The ``claude:`` prefix must be byte-identical across all three boundaries."""

    def test_shim_exists(self) -> None:
        self.assertTrue(SHIM.is_file(), f"hook shim missing at {SHIM}")

    def test_shim_prefix_matches_adapter_constant(self) -> None:
        self.assertEqual(_shim_source_prefix(_shim_text()), _CLAUDE_SOURCE_PREFIX)

    def test_billing_predicate_prefix_matches_adapter_constant(self) -> None:
        self.assertEqual(
            _predicate_claude_prefix(_DEVELOPMENT_SOURCE_PREDICATE),
            _CLAUDE_SOURCE_PREFIX,
        )

    def test_all_three_prefixes_are_identical(self) -> None:
        shim = _shim_source_prefix(_shim_text())
        predicate = _predicate_claude_prefix(_DEVELOPMENT_SOURCE_PREDICATE)
        self.assertEqual({shim, _CLAUDE_SOURCE_PREFIX, predicate}, {"claude:"})


class TestShimPinnedFlags(unittest.TestCase):
    """The flags the shim depends on must exist in the shim AND on the CLI."""

    def test_shim_references_attach_active_run(self) -> None:
        self.assertIn("--attach-active-run", _shim_text())

    def test_shim_references_payload(self) -> None:
        self.assertIn("--payload", _shim_text())

    def test_shim_references_branch(self) -> None:
        # #574: the shim forwards the session branch so the CLI can recover the
        # task id from the <task-id>-<slug> convention.
        self.assertIn("--branch", _shim_text())

    def test_shim_forwards_hook_event_type_in_payload(self) -> None:
        # #575: the hook event type must ride in the payload (it has no column),
        # keyed on hook_event_name.
        self.assertIn("hook_event_name", _shim_text())

    def test_shim_payload_keys_match_the_billing_summariser(self) -> None:
        # #710 is a two-sided string contract like the ``claude:`` prefix above:
        # the shim WRITES the session context into the payload and
        # ``summarize_session_context`` READS it back to name the timesheet row.
        # Renaming either side silently returns hook-only sessions to the bare
        # ``[/] session <task>|<event>`` name, with every test still green.
        text = _shim_text()
        self.assertIn(_PROMPT_KEY, text)
        self.assertIn(_CWD_KEY, text)

    def test_shim_records_the_transcript_path(self) -> None:
        # #710: the path (not the transcript text) is captured, so a later
        # summariser can read ~/.claude/projects/<dashed-cwd>/<session>.jsonl
        # without this hot hook path copying transcript content into every row.
        self.assertIn("transcript_path", _shim_text())

    def test_shim_caps_the_captured_prompt(self) -> None:
        # The prompt is truncated at the point of CAPTURE, so no pasted document
        # or stack trace can reach the events table however long it is.
        self.assertIn("[0:200]", _shim_text())

    def test_cli_accepts_the_exact_flags_the_shim_emits(self) -> None:
        parser = cli._build_parser()
        namespace = parser.parse_args(
            [
                "log-event",
                "--source",
                f"{_CLAUDE_SOURCE_PREFIX}PreToolUse",
                "--attach-active-run",
                "--subject",
                "Bash",
                "--branch",
                "28788-hook-fix",
                "--payload",
                '{"session_id": "s-1", "hook_event_name": "PreToolUse"}',
            ]
        )
        self.assertTrue(namespace.attach_active_run)
        self.assertEqual(namespace.branch, "28788-hook-fix")
        self.assertEqual(
            namespace.payload, '{"session_id": "s-1", "hook_event_name": "PreToolUse"}'
        )
        self.assertEqual(namespace.source, "claude:PreToolUse")


class TestShimArgvLandsBillingEligibleRow(unittest.TestCase):
    """Drive the EXACT argv the shim emits through ``cli.main`` and assert the row
    lands with the attached task ids, the payload, and — critically — matches the
    billing predicate, so a hook event sessionizes/bills exactly as intended."""

    def setUp(self) -> None:
        self._cwd = os.getcwd()
        self._state = tempfile.mkdtemp()
        self._nongit = tempfile.mkdtemp()
        os.environ["ODOO_TASK_TRACKER_DIR"] = self._state
        provision_schema(tracker_db_path(self._state))
        os.chdir(self._nongit)

    def tearDown(self) -> None:
        os.chdir(self._cwd)
        os.environ.pop("ODOO_TASK_TRACKER_DIR", None)
        shutil.rmtree(self._state, ignore_errors=True)
        shutil.rmtree(self._nongit, ignore_errors=True)

    def _run_shim_argv(self, subject: str, payload: str) -> None:
        """Invoke ``cli.main`` with the argv ``claude-event-hook`` assembles for a
        PreToolUse event: ``log-event --source claude:PreToolUse
        --attach-active-run --subject <tool> --payload <json>``."""
        argv = [
            "odoo-sdk",
            "log-event",
            "--source",
            f"{_CLAUDE_SOURCE_PREFIX}PreToolUse",
            "--attach-active-run",
            "--subject",
            subject,
            "--payload",
            payload,
        ]
        with (
            patch("sys.argv", argv),
            patch("sys.stderr", StringIO()),
            patch("sys.stdout", StringIO()),
        ):
            cli.main()

    def test_full_hook_vector_lands_a_billing_eligible_row(self) -> None:
        db = TaskStateDB()
        db.create_run(101, "Task A", 1, "Proj")
        db.create_run(202, "Task B", 1, "Proj")

        self._run_shim_argv("Bash", '{"session_id": "s-1", "tool_name": "Bash"}')

        events = TaskStateDB().get_events()
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.source, "claude:PreToolUse")
        self.assertEqual(event.subject, "Bash")
        # --attach-active-run must have attached BOTH active runs' task ids.
        self.assertEqual(sorted(event.task_ids), ["101", "202"])
        # --payload must have persisted verbatim, alongside the attachment-only
        # provenance the command records for the ids rule 2 alone attributed
        # (#779) — the marker is additive and never rewrites a caller's keys.
        self.assertEqual(
            event.payload,
            {
                "session_id": "s-1",
                "tool_name": "Bash",
                ATTACHED_TASK_IDS_PAYLOAD_KEY: event.task_ids,
            },
        )

        # The persisted row must satisfy the billing predicate — this is what
        # makes a hook event sessionize/bill. A prefix drift would persist a row
        # that never bills; assert the real predicate matches it.
        conn = sqlite3.connect(str(tracker_db_path(self._state)))
        try:
            (count,) = conn.execute(
                f"SELECT COUNT(*) FROM events WHERE {_DEVELOPMENT_SOURCE_PREDICATE}"
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(count, 1)

    def test_captured_prompt_names_the_session_end_to_end(self) -> None:
        # #710 across the whole vector: the shim's payload goes through the real
        # CLI into the real events table, and the billing summariser reads it
        # back as the session's narrative. Before this a hook-only session had
        # nothing but its tool tally to be named by.
        db = TaskStateDB()
        db.create_run(101, "Task A", 1, "Proj")
        self._run_shim_argv(
            "",
            '{"session_id": "s-1", "hook_event_name": "UserPromptSubmit", '
            '"cwd": "/workspaces/acme", '
            '"prompt": "Add a VAT column to the invoice report"}',
        )
        events = TaskStateDB().get_events()
        self.assertEqual(
            summarize_session_context(events),
            "prompt: Add a VAT column to the invoice report",
        )

    def test_stated_branch_recovers_task_without_an_active_run(self) -> None:
        # #574 end to end: with no active run, the shim's --branch lets the CLI
        # recover the task id from the <task-id>-<slug> convention, so the row
        # bills against the task instead of landing untargeted in triage. The cwd
        # is non-git (repo/branch resolve empty), proving attribution rides on the
        # STATED branch, not the working tree.
        argv = [
            "odoo-sdk",
            "log-event",
            "--source",
            f"{_CLAUDE_SOURCE_PREFIX}PostToolUse",
            "--attach-active-run",
            "--subject",
            "Bash",
            "--branch",
            "28788-hook-fix",
            "--payload",
            '{"session_id": "s-1", "hook_event_name": "PostToolUse"}',
        ]
        with (
            patch("sys.argv", argv),
            patch("sys.stderr", StringIO()),
            patch("sys.stdout", StringIO()),
        ):
            cli.main()

        events = TaskStateDB().get_events()
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.task_ids, ["28788"])
        self.assertEqual(event.branch, "28788-hook-fix")
        self.assertEqual(
            event.payload, {"session_id": "s-1", "hook_event_name": "PostToolUse"}
        )


if __name__ == "__main__":
    unittest.main()
