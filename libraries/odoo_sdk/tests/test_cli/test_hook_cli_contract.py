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
from odoo_sdk.state import LocalStateClient as TaskStateDB
from odoo_sdk.state.db import _DEVELOPMENT_SOURCE_PREDICATE, tracker_db_path
from tests.support import provision_schema

# Repo root is four parents up from tests/test_cli/<this file> (mirrors the path
# math in test_init_script_parity.py, which reaches sibling top-level dirs).
_REPO_ROOT = Path(__file__).resolve().parents[4]
SHIM = _REPO_ROOT / "devcontainer-features" / "src" / "personal-features" / "claude-event-hook"


def _shim_text() -> str:
    return SHIM.read_text(encoding="utf-8")


def _shim_source_prefix(text: str) -> str:
    """Extract the literal the shim splices before ``$HOOK_NAME`` in ``--source``.

    Matches ``--source "claude:$HOOK_NAME"`` and returns ``claude:`` — everything
    inside the quotes up to (but excluding) the first ``$`` or closing quote.
    """
    match = re.search(r'--source\s+"([^"$]*)', text)
    assert match is not None, f"no --source \"...\" argument found in {SHIM}"
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
                "--payload",
                '{"session_id": "s-1"}',
            ]
        )
        self.assertTrue(namespace.attach_active_run)
        self.assertEqual(namespace.payload, '{"session_id": "s-1"}')
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
        # --payload must have persisted verbatim.
        self.assertEqual(
            event.payload, {"session_id": "s-1", "tool_name": "Bash"}
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


if __name__ == "__main__":
    unittest.main()
