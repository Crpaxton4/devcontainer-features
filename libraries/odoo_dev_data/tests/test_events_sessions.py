"""Tests for the query-time session derivation in ``EventStore``.

``derive_sessions_overlapping`` computes gap-based sessionization directly over
the ``events`` table (no stored sessions), so these tests seed raw events and
assert the derived windows.
"""

from datetime import datetime, time, timedelta, timezone

from odoo_dev_data.events import (
    AGENTLESS_REPO,
    EventRecord,
    _DERIVE_SESSIONS_SQL,
    _derivation_margin,
    _normalize_utc_isoformat,
    _widen,
    format_repo_label,
    session_key,
)

UTC = timezone.utc
GAP = 3600  # one hour


def _event(
    store,
    *,
    ts,
    source="agent",
    task_ids=("101",),
    repo="",
    pr_num=0,
):
    return store.add_event(
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


def _derive(store, **kwargs):
    lo, hi = _whole_range()
    return store.derive_sessions_overlapping(lo, hi, gap_secs=GAP, **kwargs)


class TestGapBoundaries:
    def test_events_exactly_gap_apart_stay_one_session(self, store):
        base = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
        _event(store, ts=base)
        _event(store, ts=base + timedelta(seconds=GAP))  # exactly the gap
        windows = _derive(store)
        assert len(windows) == 1
        assert len(windows[0].event_ids) == 2

    def test_events_one_second_past_gap_split(self, store):
        base = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
        _event(store, ts=base)
        _event(store, ts=base + timedelta(seconds=GAP + 1))
        assert len(_derive(store)) == 2


class TestOverlapSemantics:
    def test_cross_day_session_returned_whole_through_partial_window(self, store):
        _event(store, ts=datetime(2026, 6, 1, 23, 30, tzinfo=UTC))
        _event(store, ts=datetime(2026, 6, 2, 0, 30, tzinfo=UTC))
        lo = datetime(2026, 6, 1, tzinfo=UTC)
        hi = datetime(2026, 6, 2, tzinfo=UTC)
        windows = store.derive_sessions_overlapping(lo, hi, gap_secs=GAP)
        assert len(windows) == 1
        assert windows[0].started_at.hour == 23
        assert windows[0].ended_at.day == 2

    def test_session_outside_window_excluded(self, store):
        _event(store, ts=datetime(2026, 6, 5, 9, 0, tzinfo=UTC))
        lo = datetime(2026, 6, 1, tzinfo=UTC)
        hi = datetime(2026, 6, 2, tzinfo=UTC)
        assert store.derive_sessions_overlapping(lo, hi, gap_secs=GAP) == []


class TestTaskOnlyPartition:
    """Sessions partition by task only; repo is display metadata."""

    def test_agent_and_commit_for_one_task_derive_one_session(self, store):
        base = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
        _event(store, ts=base, repo="", task_ids=["101"])
        _event(
            store,
            ts=base + timedelta(minutes=5),
            repo="owner/repo",
            source="commit",
            task_ids=["101"],
        )
        windows = _derive(store)
        assert len(windows) == 1
        assert len(windows[0].event_ids) == 2
        # The real repo label wins as display metadata over the absent repo.
        assert windows[0].repo == "owner/repo"

    def test_repo_less_only_session_reports_absent_repo(self, store):
        _event(store, ts=datetime(2026, 6, 1, 9, 0, tzinfo=UTC), repo="")
        windows = _derive(store)
        assert len(windows) == 1
        assert windows[0].repo == AGENTLESS_REPO == ""
        assert "\x00" not in windows[0].repo

    def test_different_tasks_same_span_stay_concurrent(self, store):
        base = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
        _event(store, ts=base, task_ids=["101"], repo="owner/repo")
        _event(
            store, ts=base + timedelta(minutes=5), task_ids=["202"], repo="owner/repo"
        )
        windows = _derive(store)
        assert len(windows) == 2
        assert {w.task_id for w in windows} == {"101", "202"}

    def test_repo_filter_selects_on_display_repo(self, store):
        base = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
        _event(store, ts=base, task_ids=["101"], repo="owner/repo")
        _event(store, ts=base, task_ids=["202"], repo="")
        windows = _derive(store, repo="owner/repo")
        assert len(windows) == 1
        assert windows[0].task_id == "101"

    def test_repo_filter_selects_agentless_group(self, store):
        base = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
        _event(store, ts=base, task_ids=["101"], repo="owner/repo")
        _event(store, ts=base, task_ids=["202"], repo="")
        windows = _derive(store, repo=AGENTLESS_REPO)
        assert len(windows) == 1
        assert windows[0].repo == AGENTLESS_REPO
        assert windows[0].task_id == "202"


class TestFormatRepoLabel:
    def test_real_label_passes_through_unchanged(self):
        assert format_repo_label("owner/repo") == "owner/repo"

    def test_absent_repo_becomes_printable_stand_in(self):
        assert format_repo_label(AGENTLESS_REPO) == "(agent)"
        assert format_repo_label("") == "(agent)"

    def test_none_is_treated_as_absent(self):
        assert format_repo_label(None) == "(agent)"

    def test_label_is_always_printable(self):
        for repo in ("owner/repo", AGENTLESS_REPO, None):
            assert "\x00" not in format_repo_label(repo)


class TestSourceAndTaskFiltering:
    def test_multi_task_event_fans_out_to_every_task(self, store):
        _event(store, ts=datetime(2026, 6, 1, 9, 0, tzinfo=UTC), task_ids=["55", "66"])
        assert {w.task_id for w in _derive(store)} == {"55", "66"}

    def test_empty_task_ids_excluded(self, store):
        _event(store, ts=datetime(2026, 6, 1, 9, 0, tzinfo=UTC), task_ids=[])
        assert _derive(store) == []

    def test_claude_hook_source_included(self, store):
        _event(
            store,
            ts=datetime(2026, 6, 1, 9, 0, tzinfo=UTC),
            source="claude:PostToolUse",
        )
        assert len(_derive(store)) == 1

    def test_merge_source_excluded(self, store):
        # ``merge`` is audit-only: a merge marker never forms a session.
        _event(store, ts=datetime(2026, 6, 1, 9, 0, tzinfo=UTC), source="merge")
        assert _derive(store) == []

    def test_pr_opened_source_included(self, store):
        _event(store, ts=datetime(2026, 6, 1, 9, 0, tzinfo=UTC), source="pr_opened")
        windows = _derive(store)
        assert len(windows) == 1
        assert windows[0].category == "Review"

    def test_task_filter_narrows(self, store):
        _event(store, ts=datetime(2026, 6, 1, 9, 0, tzinfo=UTC), task_ids=["101"])
        _event(store, ts=datetime(2026, 6, 1, 15, 0, tzinfo=UTC), task_ids=["202"])
        windows = _derive(store, task_id="202")
        assert len(windows) == 1
        assert windows[0].task_id == "202"


class TestReviewFamilyWindowed:
    """``review``/``comment``/``pr_opened`` events form WINDOWED sessions."""

    def test_comment_burst_derives_one_windowed_session(self, store):
        # 33 inline comments over 2h on one task are ONE ~2h session.
        base = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
        for i in range(33):
            _event(
                store,
                ts=base + timedelta(minutes=i * 120 // 32),
                source="comment",
                task_ids=["101"],
                repo="owner/repo",
            )
        windows = _derive(store)
        assert len(windows) == 1
        assert len(windows[0].event_ids) == 33
        assert windows[0].duration_seconds == 120 * 60
        assert windows[0].strategy_name == "review"
        assert windows[0].category == "Review"

    def test_lone_review_derives_single_event_review_session(self, store):
        event = store.add_event(
            EventRecord(
                id=None,
                source="review",
                timestamp=datetime(2026, 6, 1, 9, 0, tzinfo=UTC),
                task_ids=["101"],
                repo="owner/repo",
                pr_num=7,
            )
        )
        windows = _derive(store)
        assert len(windows) == 1
        assert windows[0].event_ids == (event.id,)
        assert windows[0].duration_seconds == 0
        assert windows[0].category == "Review"

    def test_stacked_pr_opened_burst_derives_one_windowed_session(self, store):
        base = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
        for i in range(18):
            _event(
                store,
                ts=base + timedelta(seconds=i * 24 * 60 // 17),
                source="pr_opened",
                task_ids=["101"],
                repo="owner/repo",
                pr_num=100 + i,
            )
        windows = _derive(store)
        assert len(windows) == 1
        assert len(windows[0].event_ids) == 18
        assert windows[0].duration_seconds == 24 * 60
        assert windows[0].strategy_name == "review"
        assert windows[0].category == "Review"

    def test_mixed_development_and_review_takes_development_label(self, store):
        base = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
        _event(store, ts=base, source="commit", task_ids=["101"], repo="owner/repo")
        _event(
            store,
            ts=base + timedelta(minutes=5),
            source="review",
            task_ids=["101"],
            repo="owner/repo",
        )
        windows = _derive(store)
        assert len(windows) == 1
        assert len(windows[0].event_ids) == 2
        assert windows[0].strategy_name == "development"
        assert windows[0].category == "Development"

    def test_review_and_development_split_by_gap_keep_distinct_labels(self, store):
        base = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
        _event(store, ts=base, source="commit", task_ids=["101"], repo="owner/repo")
        _event(
            store,
            ts=base + timedelta(hours=6),
            source="comment",
            task_ids=["101"],
            repo="owner/repo",
        )
        _event(
            store,
            ts=base + timedelta(hours=6, minutes=10),
            source="review",
            task_ids=["101"],
            repo="owner/repo",
        )
        windows = _derive(store)
        assert len(windows) == 2
        by_cat = {w.category: w for w in windows}
        assert set(by_cat) == {"Development", "Review"}
        assert len(by_cat["Development"].event_ids) == 1
        assert len(by_cat["Review"].event_ids) == 2


class TestMultiTaskFanOut:
    def test_shared_hook_event_extends_both_tasks_with_distinct_keys(self, store):
        base = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
        _event(store, ts=base, source="claude:PostToolUse", task_ids=["55"])
        _event(
            store,
            ts=base + timedelta(minutes=5),
            source="claude:PostToolUse",
            task_ids=["66"],
        )
        shared = _event(
            store,
            ts=base + timedelta(minutes=10),
            source="claude:PostToolUse",
            task_ids=["55", "66"],
        )
        by_task = {w.task_id: w for w in _derive(store)}
        assert set(by_task) == {"55", "66"}
        for window in by_task.values():
            assert window.started_at <= shared.timestamp <= window.ended_at
            assert shared.id in window.event_ids
        assert session_key(by_task["55"]) != session_key(by_task["66"])

    def test_multi_task_event_alone_seeds_both_task_sessions(self, store):
        shared = _event(
            store,
            ts=datetime(2026, 6, 1, 9, 0, tzinfo=UTC),
            source="claude:PostToolUse",
            task_ids=["55", "66"],
        )
        windows = _derive(store)
        assert len(windows) == 2
        for window in windows:
            assert window.event_ids == (shared.id,)


class TestIdentityStability:
    def test_min_event_id_identity_stable_under_tail_appends(self, store):
        base = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
        first = _event(store, ts=base)
        _event(store, ts=base + timedelta(minutes=10))
        before = _derive(store)[0]
        _event(store, ts=base + timedelta(minutes=20))
        after = _derive(store)[0]
        assert before.id == first.id
        assert after.id == first.id
        assert session_key(before) == session_key(after)
        assert after.event_ids == (first.id, first.id + 1, first.id + 2)

    def test_session_key_format(self, store):
        first = _event(
            store, ts=datetime(2026, 6, 1, 9, 0, tzinfo=UTC), repo="owner/repo"
        )
        assert session_key(_derive(store)[0]) == f"101|{first.id}"

    def test_job_derived_key_keeps_the_run_format(self, store):
        first = _event(store, ts=datetime(2026, 6, 1, 9, 0, tzinfo=UTC))
        window = _derive(store)[0]
        assert first.id == window.id
        window.run_session_id = "sess-1"
        assert session_key(window) == "101|run:sess-1"


class TestDerivationPrefilter:
    def test_session_straddling_widened_boundary_unaffected(self, store):
        window_start = datetime(2026, 6, 2, 0, 0, tzinfo=UTC)
        window_end = datetime(2026, 6, 3, 0, 0, tzinfo=UTC)
        first = _event(store, ts=datetime(2026, 6, 1, 23, 45, tzinfo=UTC))
        _event(store, ts=datetime(2026, 6, 2, 0, 15, tzinfo=UTC))
        windows = store.derive_sessions_overlapping(
            window_start, window_end, gap_secs=GAP
        )
        assert len(windows) == 1
        assert windows[0].id == first.id
        assert windows[0].started_at.day == 1  # not clipped to the window
        assert len(windows[0].event_ids) == 2

    def test_events_beyond_margin_are_not_scanned(self, store):
        _event(store, ts=datetime(2020, 1, 1, 9, 0, tzinfo=UTC))
        _event(store, ts=datetime(2026, 6, 2, 9, 0, tzinfo=UTC))
        windows = store.derive_sessions_overlapping(
            datetime(2026, 6, 2, tzinfo=UTC),
            datetime(2026, 6, 3, tzinfo=UTC),
            gap_secs=GAP,
        )
        assert len(windows) == 1
        assert windows[0].started_at.year == 2026

    def test_query_plan_uses_timestamp_index(self, store):
        _event(store, ts=datetime(2026, 6, 2, 9, 0, tzinfo=UTC))
        lo = datetime(2026, 6, 2, tzinfo=UTC)
        hi = datetime(2026, 6, 3, tzinfo=UTC)
        margin = _derivation_margin(GAP)
        params = {
            "agentless": AGENTLESS_REPO,
            "gap_secs": GAP,
            "start": _normalize_utc_isoformat(lo),
            "end": _normalize_utc_isoformat(hi),
            "wstart": _normalize_utc_isoformat(_widen(lo, -margin)),
            "wend": _normalize_utc_isoformat(_widen(hi, margin)),
        }
        sql = "EXPLAIN QUERY PLAN " + _DERIVE_SESSIONS_SQL.format(extra="")
        plan = "\n".join(str(row[3]) for row in store._conn.execute(sql, params))
        assert "idx_events_timestamp" in plan


class TestBoundFormatting:
    """The bound helper must emit the exact stored-timestamp string shape."""

    def test_naive_bound_gets_utc_suffix(self):
        naive = datetime(2026, 6, 1, 0, 0, 0)
        assert _normalize_utc_isoformat(naive) == "2026-06-01T00:00:00+00:00"

    def test_naive_bound_matches_stored_event_timestamp(self, store):
        instant = datetime(2026, 6, 1, 0, 0, 0)
        stored = _event(store, ts=instant)
        assert (
            _normalize_utc_isoformat(instant)
            == store.get_event(stored.id).timestamp.isoformat()
        )

    def test_aware_non_utc_bound_converted_to_utc(self):
        aware = datetime(2026, 6, 1, 8, 0, 0, tzinfo=timezone(timedelta(hours=-4)))
        assert _normalize_utc_isoformat(aware) == "2026-06-01T12:00:00+00:00"

    def test_open_range_sentinels_still_sort_past_every_row(self):
        assert _normalize_utc_isoformat(datetime.min) < "2026-06-01T00:00:00+00:00"
        assert _normalize_utc_isoformat(datetime.max) > "9998-01-01T00:00:00+00:00"


class TestLowerBoundEdge:
    """An event exactly at the inclusive lower edge is included everywhere."""

    def test_event_on_start_edge_included_consistently(self, store):
        edge = datetime(2026, 6, 1, 0, 0, 0, tzinfo=UTC)
        _event(store, ts=edge)
        lo = datetime.combine(edge.date(), time.min)  # naive midnight == edge
        hi = datetime.combine(edge.date() + timedelta(days=1), time.min)
        assert store.count_events(lo, hi) == 1
        assert len(store.get_events(lo, hi)) == 1
        windows = store.derive_sessions_overlapping(lo, hi, gap_secs=GAP)
        assert len(windows) == 1
        assert len(windows[0].event_ids) == 1

    def test_naive_and_aware_start_bounds_agree(self, store):
        edge = datetime(2026, 6, 1, 0, 0, 0, tzinfo=UTC)
        _event(store, ts=edge)
        _event(store, ts=edge + timedelta(hours=2))
        hi = datetime(2026, 6, 2, 0, 0, 0, tzinfo=UTC)
        naive_lo = datetime(2026, 6, 1, 0, 0, 0)
        aware_lo = datetime(2026, 6, 1, 0, 0, 0, tzinfo=UTC)
        assert store.count_events(naive_lo, hi) == store.count_events(aware_lo, hi)
        assert [e.id for e in store.get_events(naive_lo, hi)] == [
            e.id for e in store.get_events(aware_lo, hi)
        ]


class TestUpperBoundEdge:
    """count_events/get_events share half-open [start, end); they must agree."""

    def test_event_on_end_edge_excluded_by_both(self, store):
        _event(store, ts=datetime(2026, 6, 2, 0, 0, 0, tzinfo=UTC))
        lo = datetime(2026, 6, 1, 0, 0, 0)
        hi = datetime(2026, 6, 2, 0, 0, 0)
        assert store.count_events(lo, hi) == 0
        assert store.get_events(lo, hi) == []

    def test_count_and_get_events_agree_for_aware_non_utc_bound(self, store):
        _event(store, ts=datetime(2026, 6, 1, 11, 59, 59, tzinfo=UTC))
        # 08:00-04:00 is the same instant as 12:00+00:00.
        lo = datetime(2026, 6, 1, 8, 0, 0, tzinfo=timezone(timedelta(hours=-4)))
        hi = datetime(2026, 6, 2, 0, 0, 0, tzinfo=UTC)
        assert store.count_events(lo, hi) == 0
        assert store.get_events(lo, hi) == []


class TestDerivationEndEdge:
    """The derivation window is inclusive on ``end`` (overlap semantics)."""

    def test_session_starting_on_end_edge_included(self, store):
        _event(store, ts=datetime(2026, 6, 2, 0, 0, 0, tzinfo=UTC))
        lo = datetime(2026, 6, 1, 0, 0, 0)
        hi = datetime(2026, 6, 2, 0, 0, 0)
        windows = store.derive_sessions_overlapping(lo, hi, gap_secs=GAP)
        assert len(windows) == 1
        assert len(windows[0].event_ids) == 1

    def test_naive_and_aware_end_bounds_agree(self, store):
        _event(store, ts=datetime(2026, 6, 2, 0, 0, 0, tzinfo=UTC))
        lo = datetime(2026, 6, 1, 0, 0, 0, tzinfo=UTC)
        naive = store.derive_sessions_overlapping(
            lo, datetime(2026, 6, 2, 0, 0, 0), gap_secs=GAP
        )
        aware = store.derive_sessions_overlapping(
            lo, datetime(2026, 6, 2, 0, 0, 0, tzinfo=UTC), gap_secs=GAP
        )
        assert [w.event_ids for w in naive] == [w.event_ids for w in aware]
