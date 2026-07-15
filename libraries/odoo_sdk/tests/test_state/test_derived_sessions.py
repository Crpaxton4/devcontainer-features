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
from tests.support import make_state_db

UTC = timezone.utc
GAP = 3600  # one hour


def _tmp_state() -> LocalStateClient:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return make_state_db(Path(tmp.name))


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


class TestTaskOnlyPartition(unittest.TestCase):
    """#352: sessions partition by task only; repo is display metadata."""

    def test_agent_and_commit_for_one_task_derive_one_session(self):
        # Agent events (repo="") and resync'd commits (repo=label) for ONE task in
        # one span must collapse into a single lane, not two parallel ones.
        state = _tmp_state()
        base = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
        _event(state, ts=base, repo="", task_ids=["101"])  # agent, repo-less
        _event(state, ts=base + timedelta(minutes=5), repo="owner/repo",
               source="commit", task_ids=["101"])
        lo, hi = _whole_range()
        windows = state.derive_sessions_overlapping(lo, hi, gap_secs=GAP)
        self.assertEqual(len(windows), 1)
        self.assertEqual(len(windows[0].event_ids), 2)
        # The real repo label wins as display metadata over the repo-less sentinel.
        self.assertEqual(windows[0].repo, "owner/repo")

    def test_repo_less_only_session_displays_sentinel(self):
        state = _tmp_state()
        _event(state, ts=datetime(2026, 6, 1, 9, 0, tzinfo=UTC), repo="")
        lo, hi = _whole_range()
        windows = state.derive_sessions_overlapping(lo, hi, gap_secs=GAP)
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0].repo, AGENTLESS_REPO_SENTINEL)

    def test_different_tasks_same_span_stay_concurrent(self):
        # Scope guard: distinct tasks in one span are intended parallel billing.
        state = _tmp_state()
        base = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
        _event(state, ts=base, task_ids=["101"], repo="owner/repo")
        _event(state, ts=base + timedelta(minutes=5), task_ids=["202"], repo="owner/repo")
        lo, hi = _whole_range()
        windows = state.derive_sessions_overlapping(lo, hi, gap_secs=GAP)
        self.assertEqual(len(windows), 2)
        self.assertEqual({w.task_id for w in windows}, {"101", "202"})

    def test_repo_filter_selects_on_display_repo(self):
        state = _tmp_state()
        base = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
        _event(state, ts=base, task_ids=["101"], repo="owner/repo")
        _event(state, ts=base, task_ids=["202"], repo="")  # repo-less task
        lo, hi = _whole_range()
        windows = state.derive_sessions_overlapping(
            lo, hi, gap_secs=GAP, repo="owner/repo"
        )
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0].task_id, "101")

    def test_repo_filter_selects_sentinel_group(self):
        state = _tmp_state()
        base = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
        _event(state, ts=base, task_ids=["101"], repo="owner/repo")
        _event(state, ts=base, task_ids=["202"], repo="")
        lo, hi = _whole_range()
        windows = state.derive_sessions_overlapping(
            lo, hi, gap_secs=GAP, repo=AGENTLESS_REPO_SENTINEL
        )
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0].repo, AGENTLESS_REPO_SENTINEL)
        self.assertEqual(windows[0].task_id, "202")


class TestSourceAndTaskFiltering(unittest.TestCase):
    def test_multi_task_event_fans_out_to_every_task(self):
        # A multi-active-run event (task_ids=[55, 66]) must extend BOTH tasks'
        # sessions, not only the first.
        state = _tmp_state()
        _event(state, ts=datetime(2026, 6, 1, 9, 0, tzinfo=UTC), task_ids=["55", "66"])
        lo, hi = _whole_range()
        windows = state.derive_sessions_overlapping(lo, hi, gap_secs=GAP)
        self.assertEqual({w.task_id for w in windows}, {"55", "66"})

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


class TestMultiActiveRunFanOut(unittest.TestCase):
    def test_shared_hook_event_extends_both_tasks_with_distinct_keys(self):
        # Two active runs -> a hook event carries task_ids=[t1, t2]. Each task
        # gets its own session covering the shared event, and the two session
        # keys differ (they share the same min-event-id but differ by task).
        state = _tmp_state()
        base = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
        # Prior solo activity anchors each task's session earlier than the shared
        # event so we can prove the shared event extends an existing session.
        _event(state, ts=base, source="claude:PostToolUse", task_ids=["55"])
        _event(state, ts=base + timedelta(minutes=5), source="claude:PostToolUse", task_ids=["66"])
        shared = _event(
            state,
            ts=base + timedelta(minutes=10),
            source="claude:PostToolUse",
            task_ids=["55", "66"],
        )
        lo, hi = _whole_range()
        windows = state.derive_sessions_overlapping(lo, hi, gap_secs=GAP)
        by_task = {w.task_id: w for w in windows}
        self.assertEqual(set(by_task), {"55", "66"})
        # Both tasks' sessions cover the shared event's timestamp and embed it.
        for window in by_task.values():
            self.assertLessEqual(window.started_at, shared.timestamp)
            self.assertGreaterEqual(window.ended_at, shared.timestamp)
            self.assertIn(shared.id, window.event_ids)
        # Distinct session keys despite the shared anchor event.
        self.assertNotEqual(
            session_key(by_task["55"]), session_key(by_task["66"])
        )

    def test_multi_task_event_alone_seeds_both_task_sessions(self):
        # A lone multi-task event (no prior solo activity) still seeds a session
        # for each task; the single event id is not double-counted per session.
        state = _tmp_state()
        shared = _event(
            state,
            ts=datetime(2026, 6, 1, 9, 0, tzinfo=UTC),
            source="claude:PostToolUse",
            task_ids=["55", "66"],
        )
        lo, hi = _whole_range()
        windows = state.derive_sessions_overlapping(lo, hi, gap_secs=GAP)
        self.assertEqual(len(windows), 2)
        for window in windows:
            self.assertEqual(window.event_ids, (shared.id,))


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
        # #352: the key is task-only ``{task_id}|{min_event_id}`` — the repo is no
        # longer part of the identity, so a task's differently-repo'd events share
        # one key.
        state = _tmp_state()
        first = _event(
            state, ts=datetime(2026, 6, 1, 9, 0, tzinfo=UTC), repo="owner/repo"
        )
        lo, hi = _whole_range()
        window = state.derive_sessions_overlapping(lo, hi, gap_secs=GAP)[0]
        self.assertEqual(session_key(window), f"101|{first.id}")


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

    def test_records_task_id_and_window_bounds(self):
        state = _tmp_state()
        started = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
        ended = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
        state.record_session_upload(
            "101|5", 50, 1.0, task_id="101", started_at=started, ended_at=ended
        )
        mapping = state.get_session_upload("101|5")
        self.assertEqual(mapping["task_id"], "101")
        self.assertEqual(datetime.fromisoformat(mapping["started_at"]), started)
        self.assertEqual(datetime.fromisoformat(mapping["ended_at"]), ended)

    def test_legacy_record_leaves_bounds_null(self):
        state = _tmp_state()
        state.record_session_upload("k1", 50, 1.0)  # no bounds
        mapping = state.get_session_upload("k1")
        self.assertIsNone(mapping["task_id"])
        self.assertIsNone(mapping["started_at"])
        self.assertIsNone(mapping["ended_at"])

    def test_list_and_delete(self):
        state = _tmp_state()
        state.record_session_upload("k1", 50, 1.0)
        state.record_session_upload("k2", 60, 2.0)
        keys = {row["session_key"] for row in state.list_session_uploads()}
        self.assertEqual(keys, {"k1", "k2"})
        state.delete_session_upload("k1")
        keys = {row["session_key"] for row in state.list_session_uploads()}
        self.assertEqual(keys, {"k2"})


class TestDerivationPrefilter(unittest.TestCase):
    """#359: the base CTE prefilters events to the widened query window."""

    def test_session_straddling_widened_boundary_unaffected(self):
        # A gap-chained session whose earliest event falls before the query window
        # (but within the max(gap, 1 day) margin) must still derive whole: the
        # prefilter pulls the pre-window event in so the session is not clipped.
        state = _tmp_state()
        window_start = datetime(2026, 6, 2, 0, 0, tzinfo=UTC)
        window_end = datetime(2026, 6, 3, 0, 0, tzinfo=UTC)
        # 23:45 (day 1, before the window) chained 30min to 00:15 (day 2, inside).
        first = _event(state, ts=datetime(2026, 6, 1, 23, 45, tzinfo=UTC))
        _event(state, ts=datetime(2026, 6, 2, 0, 15, tzinfo=UTC))
        windows = state.derive_sessions_overlapping(
            window_start, window_end, gap_secs=GAP
        )
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0].id, first.id)  # whole session, earliest id
        self.assertEqual(windows[0].started_at.day, 1)  # not clipped to the window
        self.assertEqual(len(windows[0].event_ids), 2)

    def test_events_beyond_margin_are_not_scanned(self):
        # An event far outside the widened window does not appear in results.
        state = _tmp_state()
        _event(state, ts=datetime(2020, 1, 1, 9, 0, tzinfo=UTC))  # years earlier
        _event(state, ts=datetime(2026, 6, 2, 9, 0, tzinfo=UTC))  # inside window
        windows = state.derive_sessions_overlapping(
            datetime(2026, 6, 2, tzinfo=UTC),
            datetime(2026, 6, 3, tzinfo=UTC),
            gap_secs=GAP,
        )
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0].started_at.year, 2026)

    def test_query_plan_uses_timestamp_index(self):
        # EXPLAIN QUERY PLAN must show idx_events_timestamp in use, proving the
        # prefilter turned the full-table scan into an indexed range scan.
        from odoo_sdk.state.db import (
            AGENTLESS_REPO_SENTINEL,
            _DERIVE_SESSIONS_SQL,
            _bound_isoformat,
            _derivation_margin,
            _widen_lower,
            _widen_upper,
        )

        state = _tmp_state()
        _event(state, ts=datetime(2026, 6, 2, 9, 0, tzinfo=UTC))
        lo = datetime(2026, 6, 2, tzinfo=UTC)
        hi = datetime(2026, 6, 3, tzinfo=UTC)
        margin = _derivation_margin(GAP)
        params = {
            "sentinel": AGENTLESS_REPO_SENTINEL,
            "gap_secs": GAP,
            "start": _bound_isoformat(lo),
            "end": _bound_isoformat(hi),
            "wstart": _bound_isoformat(_widen_lower(lo, margin)),
            "wend": _bound_isoformat(_widen_upper(hi, margin)),
        }
        sql = "EXPLAIN QUERY PLAN " + _DERIVE_SESSIONS_SQL.format(extra="")
        with state._connect() as conn:
            plan = "\n".join(str(row[3]) for row in conn.execute(sql, params))
        self.assertIn("idx_events_timestamp", plan)


if __name__ == "__main__":
    unittest.main()
