"""Tests for :class:`LogEventCommand`, the command-layer owner of the events
append write (issue #407).

The command is the single place an ``EventRecord`` is constructed and handed to
:meth:`LocalStateClient.add_event`; the CLI ``log-event`` subcommand and the MCP
dispatch-event wrapper both route their writes through it. These tests drive the
command directly against a schema-provisioned temp DB and assert the persisted
row, so the shared write contract is pinned in one place.
"""

import unittest
from datetime import datetime, timezone
from unittest.mock import Mock

from odoo_sdk.commands import LogEventCommand
from odoo_sdk.commands.builtin import BUILTIN_COMMANDS
from tests.support import make_state_db


class TestLogEventCommand(unittest.TestCase):
    def setUp(self) -> None:
        self.db = make_state_db()

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
        # Only ``source`` is required; every other field defaults to the
        # untargeted/empty value the frontends rely on.
        LogEventCommand(state=self.db).execute(source="agent")
        event = self.db.get_events()[0]
        self.assertEqual(event.source, "agent")
        self.assertEqual(event.subject, "")
        self.assertIsNone(event.payload)
        self.assertEqual(event.task_ids, [])
        self.assertEqual(event.repo, "")

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


if __name__ == "__main__":
    unittest.main()
