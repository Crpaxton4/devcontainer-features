"""Tests for the checkpoint-cadence hint (#387)."""

import unittest
from datetime import datetime, timedelta, timezone

from odoo_sdk.state import EventRecord, LocalStateClient
from odoo_sdk.utilities.checkpoint import (
    CHECKPOINT_THRESHOLD_MINUTES,
    checkpoint_hint,
)
from tests.support import make_state_db

UTC = timezone.utc


def _note_event(db: LocalStateClient, task_id: int, when: datetime) -> None:
    """Record a ``task_note`` agent event for ``task_id`` at ``when``."""
    db.add_event(
        EventRecord(
            id=None,
            source="agent",
            timestamp=when,
            task_ids=[str(task_id)],
            repo="",
            subject="task_note",
            payload={"tool": "task_note"},
        )
    )


class TestCheckpointHint(unittest.TestCase):
    def test_suggests_checkpoint_when_last_note_is_old(self):
        db = make_state_db()
        _note_event(db, 42, datetime.now(UTC) - timedelta(minutes=20))
        hint = checkpoint_hint(db, 42, datetime.now(UTC))
        self.assertEqual(hint["minutes_since_last_note"], 20)
        self.assertTrue(hint["suggest_checkpoint"])

    def test_no_suggestion_when_last_note_is_recent(self):
        db = make_state_db()
        _note_event(db, 42, datetime.now(UTC) - timedelta(minutes=2))
        hint = checkpoint_hint(db, 42, datetime.now(UTC))
        self.assertEqual(hint["minutes_since_last_note"], 2)
        self.assertFalse(hint["suggest_checkpoint"])

    def test_falls_back_to_run_start_when_no_note_exists(self):
        db = make_state_db()
        started = datetime.now(UTC) - timedelta(minutes=18)
        hint = checkpoint_hint(db, 42, started)
        self.assertEqual(hint["minutes_since_last_note"], 18)
        self.assertTrue(hint["suggest_checkpoint"])

    def test_fresh_run_reports_zero_and_no_suggestion(self):
        db = make_state_db()
        hint = checkpoint_hint(db, 42, datetime.now(UTC))
        self.assertEqual(hint["minutes_since_last_note"], 0)
        self.assertFalse(hint["suggest_checkpoint"])

    def test_only_this_task_notes_count(self):
        db = make_state_db()
        # An old note for a *different* task must not be attributed here.
        _note_event(db, 99, datetime.now(UTC) - timedelta(minutes=30))
        started = datetime.now(UTC) - timedelta(minutes=1)
        hint = checkpoint_hint(db, 42, started)
        self.assertEqual(hint["minutes_since_last_note"], 1)
        self.assertFalse(hint["suggest_checkpoint"])

    def test_boundary_exactly_at_threshold_suggests(self):
        db = make_state_db()
        started = datetime.now(UTC) - timedelta(minutes=CHECKPOINT_THRESHOLD_MINUTES)
        hint = checkpoint_hint(db, 42, started)
        self.assertTrue(hint["suggest_checkpoint"])

    def test_missing_db_yields_empty_hint(self):
        # A client bound to a non-existent tracker DB raises on read; the hint
        # must swallow that and return {} so the calling tool still succeeds.
        db = LocalStateClient(db_path="/nonexistent/tracker.db")
        self.assertEqual(checkpoint_hint(db, 42, datetime.now(UTC)), {})

    def test_naive_reference_is_treated_as_utc(self):
        db = make_state_db()
        started = (datetime.now(UTC) - timedelta(minutes=16)).replace(tzinfo=None)
        hint = checkpoint_hint(db, 42, started)
        self.assertTrue(hint["suggest_checkpoint"])

    def test_future_reference_clamps_to_zero(self):
        db = make_state_db()
        started = datetime.now(UTC) + timedelta(minutes=5)
        hint = checkpoint_hint(db, 42, started)
        self.assertEqual(hint["minutes_since_last_note"], 0)
        self.assertFalse(hint["suggest_checkpoint"])


if __name__ == "__main__":
    unittest.main()
