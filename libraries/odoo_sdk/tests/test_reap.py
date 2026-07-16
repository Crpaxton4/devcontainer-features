"""Unit tests for the stale-run reaper helpers (:mod:`odoo_sdk.reap`, #366).

These exercise the staleness predicate, the ``last activity`` clock (latest event
for the run's task, falling back to ``started_at``), the env-driven threshold used
by the ``--attach-active-run`` exclusion, and the best-effort anchor close that
lets a reap succeed even when Odoo is unreachable.
"""

import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from odoo_sdk.state import EventRecord
from odoo_sdk.reap import (
    DEFAULT_REAP_THRESHOLD_HOURS,
    REAP_THRESHOLD_ENV,
    is_run_stale,
    reap_run,
    resolve_env_threshold_hours,
    run_last_activity,
    stale_active_runs,
    threshold_from_hours,
)
from odoo_sdk.billing.timesheet import ABORTED_ANCHOR_NAME, ANCHOR_NAME
from tests.support import make_state_db


def _hours_ago(hours: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=hours)


def _event(task_id: int, ts: datetime) -> EventRecord:
    return EventRecord(
        id=None,
        source="agent",
        timestamp=ts,
        task_ids=[str(task_id)],
        repo="",
        subject="work",
    )


class TestLatestEventTimestamp(unittest.TestCase):
    def setUp(self) -> None:
        self.db = make_state_db()

    def test_no_events_returns_none(self) -> None:
        self.assertIsNone(self.db.latest_event_timestamp_for_task(1))

    def test_returns_chronological_max(self) -> None:
        newest = _hours_ago(2)
        self.db.add_event(_event(1, _hours_ago(5)))
        self.db.add_event(_event(1, newest))
        self.db.add_event(_event(1, _hours_ago(9)))
        latest = self.db.latest_event_timestamp_for_task(1)
        self.assertLess(abs((latest - newest).total_seconds()), 2)

    def test_matches_task_inside_multi_id_array(self) -> None:
        self.db.add_event(
            EventRecord(
                id=None,
                source="agent",
                timestamp=_hours_ago(1),
                task_ids=["7", "42"],
                repo="",
                subject="fanned",
            )
        )
        self.assertIsNotNone(self.db.latest_event_timestamp_for_task(42))
        self.assertIsNone(self.db.latest_event_timestamp_for_task(999))


class TestStaleness(unittest.TestCase):
    def setUp(self) -> None:
        self.db = make_state_db()

    def _run(self, task_id: int, *, started_hours_ago: float):
        run = self.db.create_run(task_id, f"T{task_id}", 1, "Proj")
        # create_run stamps started_at=now; backdate it directly.
        with self.db._connect() as conn:  # noqa: SLF001 (test reaches into store)
            conn.execute(
                "UPDATE task_runs SET started_at = ? WHERE id = ?",
                (_hours_ago(started_hours_ago).isoformat(), run.id),
            )
        return self.db.get_run_by_id(run.id)

    def test_last_activity_falls_back_to_started_at(self) -> None:
        run = self._run(1, started_hours_ago=20)
        self.assertLess(run_last_activity(self.db, run), _hours_ago(19))

    def test_recent_event_makes_old_run_fresh(self) -> None:
        run = self._run(1, started_hours_ago=20)
        self.db.add_event(_event(1, _hours_ago(1)))
        threshold = threshold_from_hours(12)
        self.assertFalse(is_run_stale(self.db, run, threshold))

    def test_old_run_without_events_is_stale(self) -> None:
        run = self._run(1, started_hours_ago=20)
        threshold = threshold_from_hours(12)
        self.assertTrue(is_run_stale(self.db, run, threshold))

    def test_stale_active_runs_selects_only_stale(self) -> None:
        self._run(1, started_hours_ago=1)  # fresh
        self._run(2, started_hours_ago=30)  # stale
        threshold = threshold_from_hours(12)
        stale = stale_active_runs(self.db, threshold)
        self.assertEqual([run.task_id for run in stale], [2])


class TestEnvThreshold(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop(REAP_THRESHOLD_ENV, None)

    def test_default_when_unset(self) -> None:
        os.environ.pop(REAP_THRESHOLD_ENV, None)
        self.assertEqual(resolve_env_threshold_hours(), DEFAULT_REAP_THRESHOLD_HOURS)

    def test_reads_override(self) -> None:
        os.environ[REAP_THRESHOLD_ENV] = "36"
        self.assertEqual(resolve_env_threshold_hours(), 36.0)

    def test_unparseable_falls_back_to_default(self) -> None:
        os.environ[REAP_THRESHOLD_ENV] = "nonsense"
        self.assertEqual(resolve_env_threshold_hours(), DEFAULT_REAP_THRESHOLD_HOURS)

    def test_non_positive_falls_back_to_default(self) -> None:
        os.environ[REAP_THRESHOLD_ENV] = "0"
        self.assertEqual(resolve_env_threshold_hours(), DEFAULT_REAP_THRESHOLD_HOURS)

    def test_non_finite_falls_back_to_default(self) -> None:
        for raw in ("inf", "nan", "-inf"):
            os.environ[REAP_THRESHOLD_ENV] = raw
            self.assertEqual(
                resolve_env_threshold_hours(), DEFAULT_REAP_THRESHOLD_HOURS
            )

    def test_override_never_overflows_threshold(self) -> None:
        # Regression: 'inf' used to pass the >0 check and crash threshold math.
        os.environ[REAP_THRESHOLD_ENV] = "inf"
        threshold_from_hours(resolve_env_threshold_hours())  # must not raise


class TestReapRun(unittest.TestCase):
    def setUp(self) -> None:
        self.db = make_state_db()

    def _client(self, name: str) -> MagicMock:
        client = MagicMock()

        def _execute(model, method, *args, **kwargs):
            if method == "read":
                return [{"id": args[0][0], "name": name}]
            return True

        client.execute.side_effect = _execute
        return client

    def test_stamps_aborted_at_and_closes_anchor(self) -> None:
        run = self.db.create_run(1, "Wedged", 1, "Proj", timesheet_id=50)
        client = self._client(ANCHOR_NAME)
        self.assertTrue(reap_run(self.db, client, run))
        stopped = self.db.get_run_by_id(run.id)
        self.assertIsNotNone(stopped.aborted_at)
        self.assertEqual(stopped.state.value, "STOPPED")
        # The anchor row was rewritten to the aborted marker.
        client.execute.assert_any_call(
            "account.analytic.line",
            "write",
            [50],
            {"name": ABORTED_ANCHOR_NAME, "unit_amount": 0.0},
        )

    def test_edited_anchor_left_untouched(self) -> None:
        run = self.db.create_run(1, "Wedged", 1, "Proj", timesheet_id=50)
        client = self._client("Human edited this")
        self.assertFalse(reap_run(self.db, client, run))
        self.assertIsNotNone(self.db.get_run_by_id(run.id).aborted_at)

    def test_offline_odoo_still_stamps_aborted_at(self) -> None:
        run = self.db.create_run(1, "Wedged", 1, "Proj", timesheet_id=50)
        client = MagicMock()
        client.execute.side_effect = ConnectionError("odoo unreachable")
        # Best-effort: the anchor close swallows the error and reports no close,
        # but the local abort (aborted_at) still committed.
        self.assertFalse(reap_run(self.db, client, run))
        self.assertIsNotNone(self.db.get_run_by_id(run.id).aborted_at)

    def test_concurrently_stopped_run_is_a_noop(self) -> None:
        # Selected while active, then stopped by another actor before reap runs.
        run = self.db.create_run(1, "Wedged", 1, "Proj", timesheet_id=50)
        self.db.stop_run(1)
        # reap_run must not raise TaskNotRunningError; it reports no anchor close.
        self.assertFalse(reap_run(self.db, self._client(ANCHOR_NAME), run))


if __name__ == "__main__":
    unittest.main()
