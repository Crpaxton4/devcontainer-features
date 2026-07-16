"""Parity: the in-Python gap-sweep re-windowing == the SQL derivation (issue #404).

The Strategy ETL is retired; the SQL CTE ``derive_sessions_overlapping`` is the
single sessionization algorithm and ``compute_windows`` survives only as the
diagnostic gap-sweep's re-windowing. This suite pins the two together: for a
fixture event set, :func:`build_window_entries` at the production gap must bill
each task exactly what the SQL derivation + the upload billing policy bill.
"""

import unittest
from datetime import date, datetime, timezone

from odoo_sdk.adapters import load_raw_events
from odoo_sdk.sessionization import (
    SessionizationConfig,
    billable_events,
    build_window_entries,
)
from odoo_sdk.state import EventRecord
from odoo_sdk.utilities.upload import _billable_hours
from tests.support import make_state_db

UTC = timezone.utc


def _event(db, source, hour, minute, task, *, repo="acme/web", pr=0):
    db.add_event(
        EventRecord(
            id=None,
            source=source,
            timestamp=datetime(2026, 6, 1, hour, minute, tzinfo=UTC),
            task_ids=[task] if task else [],
            repo=repo,
            pr_num=pr,
        )
    )


def _sql_billed_secs_per_task(db, config):
    """Bill each SQL-derived window with the exact upload rule, summed by task."""
    windows = db.derive_sessions_overlapping(
        config.range_start, config.range_end, gap_secs=config.session_gap_secs
    )
    totals: dict[str, float] = {}
    for window in windows:
        hours = _billable_hours(
            window.duration_seconds / 3600.0,
            config.min_session_hours,
            config.round_session_hours,
        )
        totals[window.task_id] = totals.get(window.task_id, 0.0) + hours * 3600.0
    return totals


def _python_billed_secs_per_task(db, config):
    """Bill each in-Python re-windowed entry, summed by task."""
    events = load_raw_events(db, config.range_start, config.range_end)
    entries = build_window_entries(
        billable_events(events), config.session_gap_secs, config
    )
    totals: dict[str, float] = {}
    for entry in entries:
        totals[entry.task_id] = totals.get(entry.task_id, 0.0) + (
            entry.end - entry.start
        ).total_seconds()
    return totals


class TestSessionizationParity(unittest.TestCase):
    def setUp(self):
        self.db = make_state_db()
        # Task 101: a development gap-chain (commits + an agent event).
        _event(self.db, "commit", 9, 0, "101")
        _event(self.db, "commit", 9, 20, "101")
        _event(self.db, "commit", 9, 40, "101")
        _event(self.db, "agent", 9, 50, "101")
        # Task 202: a bursty review pass — five comments minutes apart. The SQL
        # windows this as ONE ~12 min session; the retired FixedDurationStrategy
        # would have over-billed it as five fixed entries.
        for minute in (0, 3, 6, 9, 12):
            _event(self.db, "review", 10, minute, "202", pr=7)
        # Task 303: a lone merge — a point-in-time release marker excluded from
        # windowed billing by BOTH engines.
        _event(self.db, "merge", 11, 0, "303", pr=8)
        self.config = SessionizationConfig(
            start_date=date(2026, 6, 1), end_date=date(2026, 6, 1)
        )

    def test_production_gap_reproduces_sql_derivation(self):
        sql = _sql_billed_secs_per_task(self.db, self.config)
        python = _python_billed_secs_per_task(self.db, self.config)
        self.assertEqual(python, sql)

    def test_merge_is_excluded_from_both_engines(self):
        sql = _sql_billed_secs_per_task(self.db, self.config)
        python = _python_billed_secs_per_task(self.db, self.config)
        self.assertNotIn("303", sql)
        self.assertNotIn("303", python)

    def test_review_burst_bills_as_one_windowed_session(self):
        # Five review comments over 12 minutes -> ONE windowed session (the
        # retired FixedDurationStrategy would have emitted five entries).
        events = load_raw_events(
            self.db, self.config.range_start, self.config.range_end
        )
        entries = build_window_entries(
            billable_events(events), self.config.session_gap_secs, self.config
        )
        review = [entry for entry in entries if entry.task_id == "202"]
        self.assertEqual(len(review), 1)
        self.assertEqual(review[0].strategy_name, "review")
        # 12 min raw span rounds under the 0.25h floor, so it bills the minimum.
        self.assertEqual(review[0].duration_secs, 900)

    def test_parity_holds_at_a_non_production_gap(self):
        # The two engines agree at any gap they are both handed, not just 60 min.
        tight = SessionizationConfig(
            start_date=date(2026, 6, 1),
            end_date=date(2026, 6, 1),
            session_gap_secs=300,
        )
        sql = _sql_billed_secs_per_task(self.db, tight)
        python = _python_billed_secs_per_task(self.db, tight)
        self.assertEqual(python, sql)


if __name__ == "__main__":
    unittest.main()
