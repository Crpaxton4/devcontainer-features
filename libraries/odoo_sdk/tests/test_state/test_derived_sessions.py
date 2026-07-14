"""Tests for the SQL-derived session read path (issue #330).

``derive_sessions_overlapping`` computes gap-based sessionization directly over
the ``events`` table at query time (no materialized ``sessions`` table), so these
tests seed raw events and assert the derived windows, plus the supporting
bookkeeping (``add_event`` timestamp normalization, ``get_events_by_ids``,
``count_events``, and the ``session_uploads`` accessors).
"""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from odoo_sdk.state import EventRecord, LocalStateClient, session_key
from odoo_sdk.state.db import AGENTLESS_REPO_SENTINEL

UTC = timezone.utc
GAP = 3600  # one hour


def _tmp_state() -> LocalStateClient:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return LocalStateClient(db_path=Path(tmp.name))


def _event(
    state,
    *,
    ts,
    source="agent",
    task_ids=("101",),
    repo="",
    pr_num=0,
):
    return state.add_event(
        EventRecord(
            id=None,
            source=source,
            timestamp=ts,
            task_ids=list(task_ids),
            repo=repo,
            pr_num=pr_num,
        )
    )


def _whole_range():
    return datetime(2020, 1, 1, tzinfo=UTC), datetime(2030, 1, 1, tzinfo=UTC)


class TestGapBoundaries(unittest.TestCase):
    def test_events_exactly_gap_apart_stay_one_session(self):
        state = _tmp_state()
        base = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
        _event(state, ts=base)
        _event(state, ts=base + timedelta(seconds=GAP))  # exactly the gap
        lo, hi = _whole_range()
        windows = state.derive_sessions_overlapping(lo, hi, gap_secs=GAP)
        self.assertEqual(len(windows), 1)
        self.assertEqual(len(windows[0].event_ids), 2)

    def test_events_one_second_past_gap_split(self):
        state = _tmp_state()
        base = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
        _event(state, ts=base)
        _event(state, ts=base + timedelta(seconds=GAP + 1))  # one second past gap
        lo, hi = _whole_range()
        windows = state.derive_sessions_overlapping(lo, hi, gap_secs=GAP)
        self.assertEqual(len(windows), 2)


class TestOverlapSemantics(unittest.TestCase):
    def test_cross_day_session_returned_whole_through_partial_window(self):
        state = _tmp_state()
        # A session that spans midnight: 23:30 day 1 .. 00:30 day 2.
        _event(state, ts=datetime(2026, 6, 1, 23, 30, tzinfo=UTC))
        _event(state, ts=datetime(2026, 6, 2, 0, 30, tzinfo=UTC))
        # Query only day 1; the whole cross-day session must still come back.
        lo = datetime(2026, 6, 1, tzinfo=UTC)
        hi = datetime(2026, 6, 2, tzinfo=UTC)
        windows = state.derive_sessions_overlapping(lo, hi, gap_secs=GAP)
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0].started_at.hour, 23)
        self.assertEqual(windows[0].ended_at.day, 2)

    def test_session_outside_window_excluded(self):
        state = _tmp_state()
        _event(state, ts=datetime(2026, 6, 5, 9, 0, tzinfo=UTC))
        lo = datetime(2026, 6, 1, tzinfo=UTC)
        hi = datetime(2026, 6, 2, tzinfo=UTC)
        self.assertEqual(state.derive_sessions_overlapping(lo, hi, gap_secs=GAP), [])


class TestRepoGrouping(unittest.TestCase):
    def test_repo_less_groups_under_sentinel_distinct_from_real_repo(self):
        state = _tmp_state()
        base = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
        _event(state, ts=base, repo="")  # repo-less
        _event(state, ts=base + timedelta(minutes=5), repo="owner/repo")
        lo, hi = _whole_range()
        windows = state.derive_sessions_overlapping(lo, hi, gap_secs=GAP)
        # Two distinct groups despite overlapping in time.
        self.assertEqual(len(windows), 2)
        repos = {w.repo for w in windows}
        self.assertIn(AGENTLESS_REPO_SENTINEL, repos)
        self.assertIn("owner/repo", repos)

    def test_repo_filter_selects_sentinel_group(self):
        state = _tmp_state()
        base = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
        _event(state, ts=base, repo="")
        _event(state, ts=base + timedelta(minutes=5), repo="owner/repo")
        lo, hi = _whole_range()
        windows = state.derive_sessions_overlapping(
            lo, hi, gap_secs=GAP, repo=AGENTLESS_REPO_SENTINEL
        )
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0].repo, AGENTLESS_REPO_SENTINEL)


class TestSourceAndTaskFiltering(unittest.TestCase):
    def test_first_task_id_selected_from_array(self):
        state = _tmp_state()
        _event(state, ts=datetime(2026, 6, 1, 9, 0, tzinfo=UTC), task_ids=["55", "66"])
        lo, hi = _whole_range()
        windows = state.derive_sessions_overlapping(lo, hi, gap_secs=GAP)
        self.assertEqual(windows[0].task_id, "55")

    def test_empty_task_ids_excluded(self):
        state = _tmp_state()
        _event(state, ts=datetime(2026, 6, 1, 9, 0, tzinfo=UTC), task_ids=[])
        lo, hi = _whole_range()
        self.assertEqual(state.derive_sessions_overlapping(lo, hi, gap_secs=GAP), [])

    def test_claude_hook_source_included(self):
        state = _tmp_state()
        _event(
            state,
            ts=datetime(2026, 6, 1, 9, 0, tzinfo=UTC),
            source="claude:PostToolUse",
        )
        lo, hi = _whole_range()
        self.assertEqual(len(state.derive_sessions_overlapping(lo, hi, gap_secs=GAP)), 1)

    def test_merge_and_review_sources_excluded(self):
        state = _tmp_state()
        _event(state, ts=datetime(2026, 6, 1, 9, 0, tzinfo=UTC), source="merge")
        _event(state, ts=datetime(2026, 6, 1, 9, 5, tzinfo=UTC), source="review")
        lo, hi = _whole_range()
        self.assertEqual(state.derive_sessions_overlapping(lo, hi, gap_secs=GAP), [])

    def test_task_filter_narrows(self):
        state = _tmp_state()
        _event(state, ts=datetime(2026, 6, 1, 9, 0, tzinfo=UTC), task_ids=["101"])
        _event(state, ts=datetime(2026, 6, 1, 15, 0, tzinfo=UTC), task_ids=["202"])
        lo, hi = _whole_range()
        windows = state.derive_sessions_overlapping(lo, hi, gap_secs=GAP, task_id="202")
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0].task_id, "202")


class TestIdentityStability(unittest.TestCase):
    def test_min_event_id_identity_stable_under_tail_appends(self):
        state = _tmp_state()
        base = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
        first = _event(state, ts=base)
        _event(state, ts=base + timedelta(minutes=10))
        lo, hi = _whole_range()
        before = state.derive_sessions_overlapping(lo, hi, gap_secs=GAP)[0]
        # Append another event inside the same session (tail write).
        _event(state, ts=base + timedelta(minutes=20))
        after = state.derive_sessions_overlapping(lo, hi, gap_secs=GAP)[0]
        # The min-event-id (and thus session_key) is unchanged by the append.
        self.assertEqual(before.id, first.id)
        self.assertEqual(after.id, first.id)
        self.assertEqual(session_key(before), session_key(after))
        self.assertEqual(after.event_ids, (first.id, first.id + 1, first.id + 2))

    def test_session_key_format(self):
        state = _tmp_state()
        first = _event(
            state, ts=datetime(2026, 6, 1, 9, 0, tzinfo=UTC), repo="owner/repo"
        )
        lo, hi = _whole_range()
        window = state.derive_sessions_overlapping(lo, hi, gap_secs=GAP)[0]
        self.assertEqual(session_key(window), f"101|owner/repo|{first.id}")


class TestAddEventNormalization(unittest.TestCase):
    def test_aware_non_utc_stored_as_utc(self):
        state = _tmp_state()
        eastern = timezone(timedelta(hours=-5))
        aware = datetime(2026, 6, 1, 9, 0, tzinfo=eastern)  # 14:00 UTC
        record = _event(state, ts=aware)
        raw = state.get_event(record.id)
        self.assertEqual(raw.timestamp.utcoffset(), timedelta(0))
        self.assertEqual(raw.timestamp.hour, 14)

    def test_naive_treated_as_utc(self):
        state = _tmp_state()
        naive = datetime(2026, 6, 1, 9, 0)  # no tzinfo
        record = _event(state, ts=naive)
        raw = state.get_event(record.id)
        self.assertEqual(raw.timestamp.utcoffset(), timedelta(0))
        self.assertEqual(raw.timestamp.hour, 9)


class TestEventBulkFetch(unittest.TestCase):
    def test_get_events_by_ids_preserves_requested_order(self):
        state = _tmp_state()
        base = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
        a = _event(state, ts=base)
        b = _event(state, ts=base + timedelta(minutes=1))
        c = _event(state, ts=base + timedelta(minutes=2))
        got = state.get_events_by_ids([c.id, a.id, b.id])
        self.assertEqual([e.id for e in got], [c.id, a.id, b.id])

    def test_get_events_by_ids_empty(self):
        self.assertEqual(_tmp_state().get_events_by_ids([]), [])

    def test_get_events_by_ids_skips_missing(self):
        state = _tmp_state()
        a = _event(state, ts=datetime(2026, 6, 1, 9, 0, tzinfo=UTC))
        got = state.get_events_by_ids([a.id, 99999])
        self.assertEqual([e.id for e in got], [a.id])


class TestCountEvents(unittest.TestCase):
    def test_count_all(self):
        state = _tmp_state()
        _event(state, ts=datetime(2026, 6, 1, 9, 0, tzinfo=UTC))
        _event(state, ts=datetime(2026, 6, 2, 9, 0, tzinfo=UTC))
        self.assertEqual(state.count_events(), 2)

    def test_count_bounded(self):
        state = _tmp_state()
        _event(state, ts=datetime(2026, 6, 1, 9, 0, tzinfo=UTC))
        _event(state, ts=datetime(2026, 6, 5, 9, 0, tzinfo=UTC))
        count = state.count_events(
            start=datetime(2026, 6, 1, tzinfo=UTC),
            end=datetime(2026, 6, 2, tzinfo=UTC),
        )
        self.assertEqual(count, 1)


class TestSessionUploads(unittest.TestCase):
    def test_record_and_get(self):
        state = _tmp_state()
        self.assertIsNone(state.get_session_upload("k1"))
        state.record_session_upload("k1", 50, 1.5)
        mapping = state.get_session_upload("k1")
        self.assertEqual(mapping["timesheet_id"], 50)
        self.assertEqual(mapping["hours"], 1.5)
        self.assertTrue(mapping["uploaded_at"])

    def test_record_is_idempotent_upsert(self):
        state = _tmp_state()
        state.record_session_upload("k1", 50, 1.0)
        state.record_session_upload("k1", 50, 2.5)  # same key, new hours
        mapping = state.get_session_upload("k1")
        self.assertEqual(mapping["timesheet_id"], 50)
        self.assertEqual(mapping["hours"], 2.5)


if __name__ == "__main__":
    unittest.main()
