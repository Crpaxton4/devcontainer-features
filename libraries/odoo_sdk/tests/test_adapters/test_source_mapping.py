"""Tests for the strict event-source to :class:`EventType` mapping."""

import unittest
from datetime import datetime, timezone

from odoo_sdk.adapters import (
    UnknownEventSourceError,
    event_record_to_raw_event,
    source_to_event_type,
)
from odoo_sdk.adapters.state_persistence import _EVENT_TYPE_TO_SOURCE
from odoo_sdk.sessionization import EventType
from odoo_sdk.state import EventRecord

UTC = timezone.utc


class TestSourceToEventType(unittest.TestCase):
    def test_known_sources(self) -> None:
        self.assertEqual(source_to_event_type("commit"), EventType.COMMIT)
        self.assertEqual(source_to_event_type("merge"), EventType.MERGE)
        self.assertEqual(source_to_event_type("review"), EventType.REVIEW)
        self.assertEqual(source_to_event_type("agent"), EventType.AGENT)

    def test_claude_prefixed_sources_map_to_hook(self) -> None:
        self.assertEqual(
            source_to_event_type("claude:SessionStart"), EventType.CLAUDE_HOOK
        )
        self.assertEqual(source_to_event_type("claude:Stop"), EventType.CLAUDE_HOOK)
        # Bare prefix still resolves (it startswith "claude:").
        self.assertEqual(source_to_event_type("claude:"), EventType.CLAUDE_HOOK)

    def test_unknown_source_raises_with_exact_message(self) -> None:
        with self.assertRaises(UnknownEventSourceError) as ctx:
            source_to_event_type("bogus")
        self.assertEqual(str(ctx.exception), "unknown event source 'bogus'")

    def test_unknown_error_is_value_error(self) -> None:
        self.assertTrue(issubclass(UnknownEventSourceError, ValueError))

    def test_reverse_map_has_synthetic_claude_hook_source(self) -> None:
        self.assertEqual(_EVENT_TYPE_TO_SOURCE[EventType.CLAUDE_HOOK], "claude:hook")


class TestEventRecordToRawEvent(unittest.TestCase):
    def _record(self, source: str) -> EventRecord:
        return EventRecord(
            id=None,
            source=source,
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            task_ids=["101"],
            repo="o/r",
        )

    def test_claude_source_becomes_hook_event(self) -> None:
        raw = event_record_to_raw_event(self._record("claude:PostToolUse"))
        self.assertEqual(raw.event_type, EventType.CLAUDE_HOOK)

    def test_unknown_source_raises_instead_of_defaulting_to_commit(self) -> None:
        with self.assertRaises(UnknownEventSourceError):
            event_record_to_raw_event(self._record("bogus"))


if __name__ == "__main__":
    unittest.main()
