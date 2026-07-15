"""Tests for the event-retention prune planner and primitives (issue #363).

These exercise the guard directly: ``plan_prune`` decides which aged events are
safe to delete, protecting every un-uploaded session (the guard) and every
session whose minimum-id key could otherwise shift, while ``execute_prune``
carries the plan out through the sole event-DELETION primitive and retires the
ledger mappings of the fully-uploaded, fully-aged sessions it removes. The
supporting :meth:`LocalStateClient.event_ids_before` / ``delete_events`` /
``vacuum`` primitives and the ``resolve_horizon`` config resolver are covered too.
"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from odoo_sdk.state import EventRecord, LocalConfig, LocalStateClient, session_key
from odoo_sdk.utilities.prune import (
    PRUNE_HORIZON_ENV_VAR,
    execute_prune,
    plan_prune,
    resolve_horizon,
)

UTC = timezone.utc
GAP = 3600  # one hour, matching the default session gap
NOW = datetime(2026, 7, 1, tzinfo=UTC)


def _tmp_state() -> LocalStateClient:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return LocalStateClient(db_path=Path(tmp.name))


def _event(state, *, ts, task_ids=("101",), source="agent"):
    return state.add_event(
        EventRecord(
            id=None,
            source=source,
            timestamp=ts,
            task_ids=list(task_ids),
            repo="",
        )
    )


def _uploaded_session(state, first_event, last_event, *, task_id, timesheet_id=500):
    """Record a ledger mapping marking a derived session fully uploaded."""
    state.record_session_upload(
        f"{task_id}|{first_event.id}",
        timesheet_id,
        1.0,
        task_id=task_id,
        started_at=first_event.timestamp,
        ended_at=last_event.timestamp,
    )


class TestPlanPruneGuard(unittest.TestCase):
    """The core guard: what may and may not be deleted."""

    def setUp(self):
        self.state = _tmp_state()
        self.config = LocalConfig()  # session_gap_secs == GAP
        self.old = NOW - timedelta(days=40)  # comfortably past a 30-day horizon

    def _plan(self, days=30):
        return plan_prune(self.state, self.config, older_than_days=days, now=NOW)

    def test_aged_uploaded_session_is_pruned_and_ledger_retired(self):
        e1 = _event(self.state, ts=self.old)
        e2 = _event(self.state, ts=self.old + timedelta(seconds=GAP))
        _uploaded_session(self.state, e1, e2, task_id="101")

        plan = self._plan()
        self.assertEqual(plan.delete_ids, [e1.id, e2.id])
        self.assertEqual(plan.retire_keys, ["101|" + str(e1.id)])

    def test_aged_un_uploaded_session_is_kept(self):
        # THE GUARD: an aged session with no ledger record must survive whole.
        e1 = _event(self.state, ts=self.old, task_ids=("202",))
        e2 = _event(self.state, ts=self.old + timedelta(seconds=GAP), task_ids=("202",))

        plan = self._plan()
        self.assertEqual(plan.delete_ids, [])
        self.assertEqual(plan.retire_keys, [])
        self.assertEqual(plan.kept_session_count, 1)
        self.assertIsNotNone(self.state.get_event(e1.id))
        self.assertIsNotNone(self.state.get_event(e2.id))

    def test_straddling_uploaded_session_is_kept_to_preserve_its_key(self):
        # A session whose events span the cutoff would have its minimum-id key
        # shifted if its aged event were deleted; it must be kept whole.
        base = self._plan().cutoff - timedelta(seconds=GAP // 2)
        e1 = _event(self.state, ts=base)  # aged
        e2 = _event(self.state, ts=base + timedelta(seconds=GAP))  # past the cutoff
        _uploaded_session(self.state, e1, e2, task_id="101")

        plan = self._plan()
        self.assertEqual(plan.delete_ids, [])
        self.assertEqual(plan.retire_keys, [])

    def test_recent_session_is_untouched(self):
        recent = NOW - timedelta(days=2)
        _event(self.state, ts=recent, task_ids=("303",))
        _event(self.state, ts=recent + timedelta(seconds=GAP), task_ids=("303",))

        self.assertEqual(self._plan().delete_ids, [])

    def test_untargeted_diagnostic_event_is_pruned(self):
        # A hook event with no task ids never forms a session, so an aged one is
        # always prunable — this is the bulk of the operational bloat.
        diag = _event(self.state, ts=self.old, task_ids=())
        plan = self._plan()
        self.assertEqual(plan.delete_ids, [diag.id])
        self.assertEqual(plan.retire_keys, [])

    def test_multi_task_shared_event_protected_by_un_uploaded_peer(self):
        # An event shared between an uploaded (101) and an un-uploaded (202)
        # session is protected, and 101 is kept whole so its key cannot shift.
        shared = _event(self.state, ts=self.old, task_ids=("101", "202"))
        tail = _event(self.state, ts=self.old + timedelta(seconds=GAP), task_ids=("101",))
        _uploaded_session(self.state, shared, tail, task_id="101")

        plan = self._plan()
        self.assertEqual(plan.delete_ids, [])
        self.assertEqual(plan.retire_keys, [])


class TestExecutePrune(unittest.TestCase):
    def setUp(self):
        self.state = _tmp_state()
        self.config = LocalConfig()
        self.old = NOW - timedelta(days=40)

    def test_execute_deletes_events_and_retires_ledger(self):
        e1 = _event(self.state, ts=self.old)
        e2 = _event(self.state, ts=self.old + timedelta(seconds=GAP))
        _uploaded_session(self.state, e1, e2, task_id="101")
        key = session_key(
            self.state.derive_sessions_overlapping(
                datetime.min, NOW, gap_secs=GAP
            )[0]
        )

        plan = plan_prune(self.state, self.config, older_than_days=30, now=NOW)
        result = execute_prune(self.state, plan)

        self.assertEqual(result, {"deleted": 2, "retired": 1, "kept_sessions": 0})
        self.assertIsNone(self.state.get_event(e1.id))
        self.assertIsNone(self.state.get_event(e2.id))
        self.assertIsNone(self.state.get_session_upload(key))

    def test_retained_window_derivation_is_unchanged(self):
        # Acceptance: the derived history for the retained window is identical
        # before and after a prune.
        recent = NOW - timedelta(days=2)
        _event(self.state, ts=recent, task_ids=("303",))
        _event(self.state, ts=recent + timedelta(seconds=GAP), task_ids=("303",))
        e1 = _event(self.state, ts=self.old)
        e2 = _event(self.state, ts=self.old + timedelta(seconds=GAP))
        _uploaded_session(self.state, e1, e2, task_id="101")

        lo, hi = NOW - timedelta(days=5), NOW + timedelta(days=1)
        before = self.state.derive_sessions_overlapping(lo, hi, gap_secs=GAP)
        snapshot = [(w.task_id, w.id, w.event_ids) for w in before]

        plan = plan_prune(self.state, self.config, older_than_days=30, now=NOW)
        execute_prune(self.state, plan)

        after = self.state.derive_sessions_overlapping(lo, hi, gap_secs=GAP)
        self.assertEqual(snapshot, [(w.task_id, w.id, w.event_ids) for w in after])

    def test_execute_without_deletions_skips_vacuum(self):
        # An un-uploaded aged session yields nothing to delete; execute is a no-op.
        _event(self.state, ts=self.old, task_ids=("202",))
        plan = plan_prune(self.state, self.config, older_than_days=30, now=NOW)
        result = execute_prune(self.state, plan)
        self.assertEqual(result["deleted"], 0)


class TestPruneDbPrimitives(unittest.TestCase):
    def setUp(self):
        self.state = _tmp_state()

    def test_event_ids_before_is_strict_and_ordered(self):
        cutoff = datetime(2026, 6, 1, tzinfo=UTC)
        older = _event(self.state, ts=cutoff - timedelta(seconds=1))
        _event(self.state, ts=cutoff)  # exactly the cutoff is NOT older
        _event(self.state, ts=cutoff + timedelta(seconds=1))
        self.assertEqual(self.state.event_ids_before(cutoff), [older.id])

    def test_delete_events_empty_is_noop(self):
        self.assertEqual(self.state.delete_events([]), 0)

    def test_delete_events_chunks_large_id_lists(self):
        # More ids than the delete chunk size, to exercise the batching loop.
        base = datetime(2026, 1, 1, tzinfo=UTC)
        ids = [
            _event(self.state, ts=base + timedelta(seconds=i)).id for i in range(1100)
        ]
        deleted = self.state.delete_events(ids)
        self.assertEqual(deleted, 1100)
        self.assertEqual(self.state.count_events(), 0)

    def test_vacuum_runs_cleanly(self):
        _event(self.state, ts=datetime(2026, 1, 1, tzinfo=UTC))
        self.state.delete_events(self.state.event_ids_before(NOW))
        self.state.vacuum()  # must not raise
        self.assertEqual(self.state.count_events(), 0)

    def test_vacuum_swallows_a_locked_database(self):
        # Reclaim is non-essential, so a lock held by a concurrent writer must be
        # swallowed rather than crashing a prune that already committed.
        import sqlite3

        with patch("odoo_sdk.state.db.sqlite3.connect") as connect:
            conn = connect.return_value
            conn.execute.side_effect = [
                None,  # PRAGMA busy_timeout
                sqlite3.OperationalError("database is locked"),  # VACUUM
            ]
            self.state.vacuum()  # must not raise
        conn.close.assert_called_once()


class TestResolveHorizon(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.pop(PRUNE_HORIZON_ENV_VAR, None)

    def tearDown(self):
        if self._saved is not None:
            os.environ[PRUNE_HORIZON_ENV_VAR] = self._saved
        else:
            os.environ.pop(PRUNE_HORIZON_ENV_VAR, None)

    def test_default_is_off(self):
        self.assertIsNone(resolve_horizon(LocalConfig()))

    def test_env_var_horizon(self):
        os.environ[PRUNE_HORIZON_ENV_VAR] = "45"
        self.assertEqual(resolve_horizon(LocalConfig()), 45)

    def test_zero_and_invalid_are_off(self):
        os.environ[PRUNE_HORIZON_ENV_VAR] = "0"
        self.assertIsNone(resolve_horizon(LocalConfig()))
        os.environ[PRUNE_HORIZON_ENV_VAR] = "not-a-number"
        self.assertIsNone(resolve_horizon(LocalConfig()))

    def test_file_behavior_key_wins_over_env(self):
        tmp = tempfile.mkdtemp()
        path = Path(tmp) / "config.toml"
        path.write_text("[behavior]\nprune_horizon_days = 90\n")
        os.environ[PRUNE_HORIZON_ENV_VAR] = "45"
        self.assertEqual(resolve_horizon(LocalConfig.load(config_path=str(path))), 90)


if __name__ == "__main__":
    unittest.main()
