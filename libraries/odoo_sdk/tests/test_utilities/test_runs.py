"""Tests for the run-table projection in :mod:`odoo_sdk.utilities.runs`.

The projection's contract since #506 is that ``elapsed`` is the *billed* clock:
it comes from sessionization (the same derivation the upload path bills from)
whenever a state client and config are supplied, and only degrades to the FSM's
wall-clock subtraction when they are not — and says so when it does.

Sessions are derived from a real schema-provisioned state DB, never mocked, so
the tests exercise the same SQL derivation the billing path uses.
"""

import json
import unittest
from datetime import datetime, timedelta, timezone

from odoo_sdk.state import EventRecord, LocalConfig, TaskRun, TaskState
from odoo_sdk.utilities.runs import format_elapsed, run_summary, sessionized_elapsed
from tests.support import make_state_db

UTC = timezone.utc


def _config() -> LocalConfig:
    # A 60-minute inactivity threshold, so the stopped gap between the two run
    # rows below is bridged into a single session (the #506 scenario).
    return LocalConfig(behavior={"session_gap_mins": 60})


def _commit(state, hour, minute=0, task="27480"):
    return state.add_event(
        EventRecord(
            id=None,
            source="commit",
            timestamp=datetime(2026, 7, 20, hour, minute, tzinfo=UTC),
            task_ids=[task],
            repo="o/r",
        )
    )


def _run(started, stopped, *, run_id=1, task_id=27480, state=TaskState.STOPPED):
    return TaskRun(
        id=run_id,
        task_id=task_id,
        task_name="Kiosk self-service",
        project_id=5,
        project_name="Acme",
        state=state,
        started_at=started,
        stopped_at=stopped,
        timesheet_id=None,
        notes=[],
    )


def _split_effort_state():
    """One effort's events spanning the stopped gap between two run rows.

    Commits at 09:00, 09:15, 09:34 — all within the 60-minute gap, so the SQL
    derivation returns them as a single 34-minute session even though the FSM
    stopped and restarted in the middle.
    """
    state = make_state_db()
    for hour, minute in ((9, 0), (9, 15), (9, 34)):
        _commit(state, hour, minute)
    return state


class TestFormatElapsed(unittest.TestCase):
    def test_renders_hours_minutes_seconds(self):
        self.assertEqual(format_elapsed(3661), "1h 1m 1s")

    def test_truncates_to_whole_seconds(self):
        self.assertEqual(format_elapsed(59.9), "0h 0m 59s")

    def test_zero_is_a_full_string_not_an_empty_one(self):
        self.assertEqual(format_elapsed(0), "0h 0m 0s")


class TestSessionizedElapsed(unittest.TestCase):
    def test_bridged_gap_is_measured_as_one_effort(self):
        state = _split_effort_state()
        run = _run(
            datetime(2026, 7, 20, 9, 0, tzinfo=UTC),
            datetime(2026, 7, 20, 9, 18, 50, tzinfo=UTC),
        )
        seconds, keys = sessionized_elapsed(run, state, _config())
        # 09:00 -> 09:34 whole, not the 18m50s the FSM row spans.
        self.assertEqual(seconds, 34 * 60)
        self.assertEqual(len(keys), 1)

    def test_both_run_rows_of_one_effort_report_the_same_duration(self):
        state = _split_effort_state()
        config = _config()
        first = _run(
            datetime(2026, 7, 20, 9, 0, tzinfo=UTC),
            datetime(2026, 7, 20, 9, 18, 50, tzinfo=UTC),
        )
        second = _run(
            datetime(2026, 7, 20, 9, 33, tzinfo=UTC),
            datetime(2026, 7, 20, 9, 34, tzinfo=UTC),
            run_id=2,
        )
        first_secs, first_keys = sessionized_elapsed(first, state, config)
        second_secs, second_keys = sessionized_elapsed(second, state, config)
        self.assertEqual(first_secs, second_secs)
        # Same effort, same session key on both rows -> the sharing is visible.
        self.assertEqual(first_keys, second_keys)

    def test_run_without_events_has_no_derived_sessions(self):
        state = make_state_db()
        run = _run(
            datetime(2026, 7, 20, 9, 0, tzinfo=UTC),
            datetime(2026, 7, 20, 9, 30, tzinfo=UTC),
        )
        self.assertEqual(sessionized_elapsed(run, state, _config()), (None, ()))

    def test_other_tasks_events_do_not_leak_in(self):
        state = _split_effort_state()
        for hour, minute in ((9, 5), (9, 40)):
            _commit(state, hour, minute, task="99999")
        run = _run(
            datetime(2026, 7, 20, 9, 0, tzinfo=UTC),
            datetime(2026, 7, 20, 9, 18, 50, tzinfo=UTC),
        )
        seconds, _ = sessionized_elapsed(run, state, _config())
        self.assertEqual(seconds, 34 * 60)

    def test_active_run_is_bounded_at_now(self):
        state = make_state_db()
        now = datetime.now(UTC)
        state.add_event(
            EventRecord(
                id=None,
                source="commit",
                timestamp=now - timedelta(minutes=10),
                task_ids=["27480"],
                repo="o/r",
            )
        )
        state.add_event(
            EventRecord(
                id=None,
                source="commit",
                timestamp=now - timedelta(minutes=2),
                task_ids=["27480"],
                repo="o/r",
            )
        )
        run = _run(now - timedelta(minutes=12), None, state=TaskState.RUNNING)
        seconds, keys = sessionized_elapsed(run, state, _config())
        self.assertEqual(round(seconds), 8 * 60)
        self.assertEqual(len(keys), 1)


class TestRunSummary(unittest.TestCase):
    def test_elapsed_comes_from_sessionization_when_state_is_supplied(self):
        state = _split_effort_state()
        run = _run(
            datetime(2026, 7, 20, 9, 0, tzinfo=UTC),
            datetime(2026, 7, 20, 9, 18, 50, tzinfo=UTC),
        )
        summary = run_summary(run, state, _config())
        self.assertEqual(summary["elapsed_source"], "sessionization")
        self.assertEqual(summary["elapsed"], "0h 34m 0s")
        self.assertEqual(summary["elapsed_seconds"], 2040.0)
        # The wall clock the FSM row spans stays visible for reference.
        self.assertEqual(summary["elapsed_wall_clock"], "0h 18m 50s")
        self.assertEqual(len(summary["session_keys"]), 1)

    def test_identity_fields_are_projected_unchanged(self):
        state = _split_effort_state()
        run = _run(
            datetime(2026, 7, 20, 9, 0, tzinfo=UTC),
            datetime(2026, 7, 20, 9, 18, 50, tzinfo=UTC),
        )
        summary = run_summary(run, state, _config())
        self.assertEqual(summary["id"], 1)
        self.assertEqual(summary["task_id"], 27480)
        self.assertEqual(summary["task_name"], "Kiosk self-service")
        self.assertEqual(summary["project_name"], "Acme")
        self.assertEqual(summary["state"], "STOPPED")

    def test_falls_back_to_wall_clock_without_a_state_client(self):
        run = _run(
            datetime(2026, 7, 20, 9, 0, tzinfo=UTC),
            datetime(2026, 7, 20, 9, 18, 50, tzinfo=UTC),
        )
        summary = run_summary(run)
        self.assertEqual(summary["elapsed_source"], "wall_clock")
        self.assertEqual(summary["elapsed"], "0h 18m 50s")
        self.assertEqual(summary["elapsed"], summary["elapsed_wall_clock"])
        self.assertEqual(summary["session_keys"], [])

    def test_config_alone_is_not_enough_to_sessionize(self):
        run = _run(
            datetime(2026, 7, 20, 9, 0, tzinfo=UTC),
            datetime(2026, 7, 20, 9, 18, 50, tzinfo=UTC),
        )
        self.assertEqual(
            run_summary(run, None, _config())["elapsed_source"], "wall_clock"
        )

    def test_run_with_no_derived_sessions_falls_back_and_says_so(self):
        state = make_state_db()  # no events at all
        run = _run(
            datetime(2026, 7, 20, 9, 0, tzinfo=UTC),
            datetime(2026, 7, 20, 9, 18, 50, tzinfo=UTC),
        )
        summary = run_summary(run, state, _config())
        self.assertEqual(summary["elapsed_source"], "wall_clock")
        self.assertEqual(summary["elapsed"], "0h 18m 50s")
        self.assertEqual(summary["session_keys"], [])

    def test_summary_is_json_serializable(self):
        state = _split_effort_state()
        run = _run(
            datetime(2026, 7, 20, 9, 0, tzinfo=UTC),
            datetime(2026, 7, 20, 9, 18, 50, tzinfo=UTC),
        )
        json.dumps(run_summary(run, state, _config()))


if __name__ == "__main__":
    unittest.main()
