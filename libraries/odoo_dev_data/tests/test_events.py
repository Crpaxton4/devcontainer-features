"""Tests for ``EventStore``: events, uploads, chatter dedupe, settings, lifecycle."""

import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import pytest

from odoo_dev_data.events import (
    AGENTLESS_REPO,
    AGENTLESS_REPO_LABEL,
    AGENTLESS_REPO_SENTINEL,
    EventRecord,
    EventStore,
    SessionWindow,
)

UTC = timezone.utc

WRITERS = 2
EVENTS_PER_WRITER = 50


def _event(minute: int, task="101", source="commit", external_id=None) -> EventRecord:
    return EventRecord(
        id=None,
        source=source,
        timestamp=datetime(2026, 6, 1, 9, minute, tzinfo=UTC),
        task_ids=[task],
        repo="owner/repo",
        pr_num=0,
        branch="main",
        subject="did work",
        payload={"pr_title": "T", "pr_body": "B"},
        external_id=external_id,
    )


def _note_event(minute: int, task="101") -> EventRecord:
    return EventRecord(
        id=None,
        source="agent",
        timestamp=datetime(2026, 6, 1, 9, minute, tzinfo=UTC),
        task_ids=[task],
        repo="",
        subject="task_note",
        payload={"tool": "task_note"},
    )


def _bare(store, *, ts, task_ids=(), source="chatter", subject=""):
    return store.add_event(
        EventRecord(
            id=None,
            source=source,
            timestamp=ts,
            task_ids=list(task_ids),
            repo="",
            subject=subject,
        )
    )


# -- vocabulary ---------------------------------------------------------------


def test_agentless_vocabulary():
    assert AGENTLESS_REPO == ""
    assert AGENTLESS_REPO_SENTINEL == AGENTLESS_REPO
    assert AGENTLESS_REPO_LABEL == "(agent)"


def test_session_window_duration():
    start = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
    window = SessionWindow(
        id=1,
        task_id="101",
        repo="",
        started_at=start,
        ended_at=start + timedelta(minutes=30),
    )
    assert window.duration_seconds == 1800
    assert window.run_session_id == ""
    assert window.has_failed is False


# -- events -------------------------------------------------------------------


class TestEventStore:
    def test_add_and_get_event_roundtrips(self, store):
        stored = store.add_event(_event(0))
        assert stored.id is not None
        fetched = store.get_event(stored.id)
        assert fetched.task_ids == ["101"]
        assert fetched.payload == {"pr_title": "T", "pr_body": "B"}

    def test_get_missing_event_returns_none(self, store):
        assert store.get_event(999) is None

    def test_get_events_ordered_and_range_bounded(self, store):
        store.add_event(_event(30))
        store.add_event(_event(0))
        assert [e.timestamp.minute for e in store.get_events()] == [0, 30]
        bounded = store.get_events(
            start=datetime(2026, 6, 1, 9, 10, tzinfo=UTC),
            end=datetime(2026, 6, 1, 9, 45, tzinfo=UTC),
        )
        assert len(bounded) == 1

    def test_null_payload_roundtrips(self, store):
        record = _event(0)
        record.payload = None
        stored = store.add_event(record)
        assert store.get_event(stored.id).payload is None

    def test_aware_non_utc_stored_as_utc(self, store):
        aware = datetime(2026, 6, 1, 9, 0, tzinfo=timezone(timedelta(hours=-5)))
        record = _event(0)
        record.timestamp = aware
        raw = store.get_event(store.add_event(record).id)
        assert raw.timestamp.utcoffset() == timedelta(0)
        assert raw.timestamp.hour == 14

    def test_naive_treated_as_utc(self, store):
        record = _event(0)
        record.timestamp = datetime(2026, 6, 1, 9, 0)
        raw = store.get_event(store.add_event(record).id)
        assert raw.timestamp.utcoffset() == timedelta(0)
        assert raw.timestamp.hour == 9

    def test_get_events_by_ids_preserves_requested_order(self, store):
        a, b, c = (store.add_event(_event(m)) for m in (0, 1, 2))
        got = store.get_events_by_ids([c.id, a.id, b.id])
        assert [e.id for e in got] == [c.id, a.id, b.id]

    def test_get_events_by_ids_empty(self, store):
        assert store.get_events_by_ids([]) == []

    def test_get_events_by_ids_skips_missing(self, store):
        a = store.add_event(_event(0))
        assert [e.id for e in store.get_events_by_ids([a.id, 99999])] == [a.id]

    def test_count_all_and_bounded(self, store):
        store.add_event(_event(0))
        _bare(store, ts=datetime(2026, 6, 5, 9, 0, tzinfo=UTC))
        assert store.count_events() == 2
        count = store.count_events(
            start=datetime(2026, 6, 1, tzinfo=UTC),
            end=datetime(2026, 6, 2, tzinfo=UTC),
        )
        assert count == 1


class TestExternalId:
    def test_external_id_roundtrips(self, store):
        stored = store.add_event(_event(0, external_id="git:abc"))
        assert store.get_event(stored.id).external_id == "git:abc"

    def test_external_id_defaults_to_none(self, store):
        stored = store.add_event(_event(0))
        assert store.get_event(stored.id).external_id is None

    def test_first_insert_returns_true(self, store):
        assert store.add_event_dedup(_event(0, external_id="git:abc"))
        assert store.count_events() == 1

    def test_duplicate_external_id_returns_false(self, store):
        store.add_event_dedup(_event(0, external_id="git:abc"))
        assert not store.add_event_dedup(_event(0, task="999", external_id="git:abc"))
        assert store.count_events() == 1

    def test_null_external_ids_never_collide(self, store):
        assert store.add_event_dedup(_event(0))
        assert store.add_event_dedup(_event(0))
        assert store.count_events() == 2

    def test_add_event_returns_existing_row_on_conflict(self, store):
        first = store.add_event(_event(0, external_id="git:abc"))
        again = store.add_event(_event(0, task="999", external_id="git:abc"))
        assert again.id == first.id
        assert again.task_ids == ["101"]
        assert store.count_events() == 1


class TestLastNoteAt:
    def test_returns_none_when_no_note_event(self, store):
        store.add_event(_event(0))
        assert store.last_note_at(101) is None

    def test_returns_most_recent_note_timestamp(self, store):
        store.add_event(_note_event(5))
        store.add_event(_note_event(30))
        assert store.last_note_at(101) == datetime(2026, 6, 1, 9, 30, tzinfo=UTC)

    def test_ignores_notes_for_other_tasks(self, store):
        store.add_event(_note_event(30, task="999"))
        assert store.last_note_at(101) is None

    def test_matches_multi_task_note_event(self, store):
        record = _note_event(12)
        record.task_ids = ["101", "202"]
        store.add_event(record)
        assert store.last_note_at(202) == datetime(2026, 6, 1, 9, 12, tzinfo=UTC)

    def test_ignores_non_note_agent_events(self, store):
        record = _note_event(20)
        record.subject = "start_task"
        store.add_event(record)
        assert store.last_note_at(101) is None


class TestGetTaskEvents:
    """Per-task audit event reads with inclusive window bounds."""

    def _add(self, store, task_ids, ts, subject="tool"):
        store.add_event(
            EventRecord(
                id=None,
                source="agent",
                timestamp=ts,
                task_ids=task_ids,
                repo="",
                subject=subject,
            )
        )

    def test_returns_only_the_tasks_events_in_order(self, store):
        self._add(store, ["1"], datetime(2026, 8, 1, 10, 0, tzinfo=UTC), "b")
        self._add(store, ["1"], datetime(2026, 8, 1, 9, 0, tzinfo=UTC), "a")
        self._add(store, ["2"], datetime(2026, 8, 1, 9, 30, tzinfo=UTC), "other")
        assert [e.subject for e in store.get_task_events("1")] == ["a", "b"]

    def test_window_bounds_are_inclusive(self, store):
        start = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)
        end = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)
        self._add(store, ["1"], start, "on-start")
        self._add(store, ["1"], end, "on-end")
        self._add(store, ["1"], datetime(2026, 8, 1, 8, 59, tzinfo=UTC), "before")
        self._add(store, ["1"], datetime(2026, 8, 1, 10, 1, tzinfo=UTC), "after")
        events = store.get_task_events("1", start=start, end=end)
        assert [e.subject for e in events] == ["on-start", "on-end"]

    def test_multi_task_event_matches_each_task_once(self, store):
        ts = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)
        self._add(store, ["1", "2", "1"], ts, "shared")
        assert len(store.get_task_events("1")) == 1
        assert len(store.get_task_events("2")) == 1

    def test_no_events_returns_empty_list(self, store):
        assert store.get_task_events("404") == []


# -- triage -------------------------------------------------------------------


class TestGetUnattributedEvents:
    def test_returns_only_events_with_empty_task_ids(self, store):
        base = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
        lone = _bare(store, ts=base)
        _bare(store, ts=base + timedelta(minutes=1), task_ids=("101",))
        assert [r.id for r in store.get_unattributed_events()] == [lone.id]

    def test_orders_by_timestamp(self, store):
        base = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
        later = _bare(store, ts=base + timedelta(hours=2))
        earlier = _bare(store, ts=base)
        rows = store.get_unattributed_events()
        assert [r.id for r in rows] == [earlier.id, later.id]

    def test_window_is_half_open(self, store):
        start = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
        end = datetime(2026, 6, 2, 0, 0, tzinfo=UTC)
        inside = _bare(store, ts=datetime(2026, 6, 1, 12, 0, tzinfo=UTC))
        _bare(store, ts=start - timedelta(seconds=1))
        _bare(store, ts=end)  # exactly the exclusive upper edge
        rows = store.get_unattributed_events(start, end)
        assert [r.id for r in rows] == [inside.id]

    def test_ignores_source_predicate(self, store):
        review = _bare(
            store, ts=datetime(2026, 6, 1, 9, 0, tzinfo=UTC), source="review"
        )
        assert [r.id for r in store.get_unattributed_events()] == [review.id]


class TestAssignEventTaskIds:
    def test_attributes_every_id_in_one_call(self, store):
        base = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
        ids = [_bare(store, ts=base + timedelta(minutes=i)).id for i in range(3)]
        assert store.assign_event_task_ids(ids, 24648) == 3
        for event_id in ids:
            assert store.get_event(event_id).task_ids == ["24648"]

    def test_empty_id_list_is_noop(self, store):
        assert store.assign_event_task_ids([], 24648) == 0

    @pytest.mark.parametrize("bad", [0, -5, True])
    def test_rejects_invalid_task_id(self, store, bad):
        with pytest.raises(ValueError):
            store.assign_event_task_ids([1], bad)

    def test_chunks_more_ids_than_one_statement_holds(self, store):
        base = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
        ids = [_bare(store, ts=base + timedelta(seconds=i)).id for i in range(1100)]
        assert store.assign_event_task_ids(ids, 7) == 1100

    def test_reattributed_events_become_derivable(self, store):
        base = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
        ids = [_bare(store, ts=base + timedelta(minutes=5 * i)).id for i in range(3)]
        lo = datetime(2026, 6, 1, tzinfo=UTC)
        hi = datetime(2026, 6, 2, tzinfo=UTC)
        assert store.derive_sessions_overlapping(lo, hi, gap_secs=3600) == []
        store.assign_event_task_ids(ids, 24648)
        sessions = store.derive_sessions_overlapping(lo, hi, gap_secs=3600)
        assert len(sessions) == 1
        assert sessions[0].task_id == "24648"
        assert sessions[0].event_ids == tuple(ids)


# -- upload ledger, chatter dedupe, settings ----------------------------------


class TestSessionUploads:
    def test_record_and_get(self, store):
        assert store.get_session_upload("k1") is None
        store.record_session_upload("k1", 50, 1.5)
        mapping = store.get_session_upload("k1")
        assert mapping["timesheet_id"] == 50
        assert mapping["hours"] == 1.5
        assert mapping["uploaded_at"]

    def test_record_is_idempotent_upsert(self, store):
        store.record_session_upload("k1", 50, 1.0)
        store.record_session_upload("k1", 50, 2.5)
        mapping = store.get_session_upload("k1")
        assert mapping["timesheet_id"] == 50
        assert mapping["hours"] == 2.5

    def test_records_task_id_and_window_bounds(self, store):
        started = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
        ended = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
        store.record_session_upload(
            "101|5", 50, 1.0, task_id="101", started_at=started, ended_at=ended
        )
        mapping = store.get_session_upload("101|5")
        assert mapping["task_id"] == "101"
        assert datetime.fromisoformat(mapping["started_at"]) == started
        assert datetime.fromisoformat(mapping["ended_at"]) == ended

    def test_record_without_bounds_leaves_them_null(self, store):
        store.record_session_upload("k1", 50, 1.0)
        mapping = store.get_session_upload("k1")
        assert mapping["task_id"] is None
        assert mapping["started_at"] is None
        assert mapping["ended_at"] is None

    def test_list_and_delete(self, store):
        store.record_session_upload("k1", 50, 1.0)
        store.record_session_upload("k2", 60, 2.0)
        assert {r["session_key"] for r in store.list_session_uploads()} == {"k1", "k2"}
        store.delete_session_upload("k1")
        assert {r["session_key"] for r in store.list_session_uploads()} == {"k2"}

    def test_remap_timesheet_id(self, store):
        store.record_session_upload("k1", 50, 1.0)
        store.record_session_upload("k2", 50, 2.0)
        store.record_session_upload("k3", 60, 3.0)
        store.remap_timesheet_id(50, 77)
        assert store.get_session_upload("k1")["timesheet_id"] == 77
        assert store.get_session_upload("k2")["timesheet_id"] == 77
        assert store.get_session_upload("k3")["timesheet_id"] == 60


class TestChatterDedupe:
    def test_first_record_wins(self, store):
        assert store.get_chatter_dedupe(1, "k") is None
        assert store.record_chatter_dedupe(1, "k", 500) is True
        assert store.record_chatter_dedupe(1, "k", 999) is False
        assert store.get_chatter_dedupe(1, "k") == 500

    def test_keys_are_scoped_per_task(self, store):
        store.record_chatter_dedupe(1, "k", 500)
        assert store.get_chatter_dedupe(2, "k") is None
        assert store.record_chatter_dedupe(2, "k", 600) is True
        assert store.get_chatter_dedupe(2, "k") == 600


class TestSettings:
    def test_get_missing_is_none(self, store):
        assert store.get_setting("gap") is None

    def test_set_then_overwrite(self, store):
        store.set_setting("gap", "3600")
        assert store.get_setting("gap") == "3600"
        store.set_setting("gap", "1800")
        assert store.get_setting("gap") == "1800"


# -- lifecycle and concurrency ------------------------------------------------


class TestLifecycle:
    def test_context_manager_closes_the_connection(self, tmp_path):
        with EventStore(home=tmp_path) as store:
            store.add_event(_event(0))
        with pytest.raises(sqlite3.ProgrammingError):
            store.count_events()

    def test_close_checkpoints_the_wal(self, tmp_path):
        store = EventStore(home=tmp_path)
        store.add_event(_event(0))
        store.close()
        wal = tmp_path / "state.db-wal"
        assert not wal.exists() or wal.stat().st_size == 0
        # The committed write is readable from the main file alone.
        conn = sqlite3.connect(tmp_path / "state.db")
        try:
            assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
        finally:
            conn.close()

    def test_two_stores_on_one_home_see_each_others_writes(self, tmp_path):
        with EventStore(home=tmp_path) as first, EventStore(home=tmp_path) as second:
            stored = first.add_event(_event(0))
            assert second.get_event(stored.id).task_ids == ["101"]
            second.set_setting("k", "v")
            assert first.get_setting("k") == "v"

    def test_shared_connection_is_used_as_given(self, tmp_path):
        with EventStore(home=tmp_path) as owner:
            other = EventStore(owner._conn)
            other.add_event(_event(0))
            assert owner.count_events() == 1

    def test_connection_is_wal_with_busy_timeout(self, store):
        assert store._conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert store._conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000

    def test_failed_write_rolls_back_and_store_stays_usable(self, store):
        with pytest.raises(RuntimeError):
            with store._write() as conn:
                conn.execute(
                    "INSERT INTO events (source, timestamp) VALUES ('commit', ?)",
                    (datetime(2026, 6, 1, tzinfo=UTC).isoformat(),),
                )
                raise RuntimeError("boom")
        assert store.count_events() == 0
        store.add_event(_event(0))
        assert store.count_events() == 1

    def test_write_holds_the_write_lock(self, tmp_path, store):
        with store._write() as conn:
            assert conn.in_transaction
            rival = sqlite3.connect(tmp_path / "state.db")
            try:
                rival.execute("PRAGMA busy_timeout=0")
                with pytest.raises(sqlite3.OperationalError):
                    rival.execute("BEGIN IMMEDIATE")
            finally:
                rival.close()


class TestConcurrentWriters:
    def test_separate_stores_no_silent_drops(self, tmp_path):
        """Writers on their own stores hammer ``add_event`` on one home."""
        EventStore(home=tmp_path).close()  # create and migrate once
        barrier = threading.Barrier(WRITERS)
        errors: list[Exception] = []

        def hammer(writer: int) -> None:
            with EventStore(home=tmp_path) as own:
                barrier.wait()
                try:
                    for seq in range(EVENTS_PER_WRITER):
                        own.add_event(_event(seq % 60, task=str(writer)))
                except Exception as exc:  # pragma: no cover - failure path
                    errors.append(exc)

        threads = [threading.Thread(target=hammer, args=(w,)) for w in range(WRITERS)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert errors == []
        with EventStore(home=tmp_path) as check:
            assert check.count_events() == WRITERS * EVENTS_PER_WRITER

    def test_threads_share_one_store(self, store):
        barrier = threading.Barrier(WRITERS)
        errors: list[Exception] = []

        def hammer(writer: int) -> None:
            barrier.wait()
            try:
                for seq in range(EVENTS_PER_WRITER):
                    store.add_event(_event(seq % 60, task=str(writer)))
                    store.count_events()
            except Exception as exc:  # pragma: no cover - failure path
                errors.append(exc)

        threads = [threading.Thread(target=hammer, args=(w,)) for w in range(WRITERS)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert errors == []
        assert store.count_events() == WRITERS * EVENTS_PER_WRITER
