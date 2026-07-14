import tempfile
import time
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from odoo_sdk.adapters import (
    event_record_to_raw_event,
    ingest_events_incrementally,
    load_raw_events,
    persist_session_windows,
    raw_event_to_event_record,
    time_entry_to_session_window,
)
from odoo_sdk.sessionization import (
    AGENTLESS_REPO_SENTINEL,
    EventType,
    RawEvent,
    SessionizationConfig,
    transform,
)
from odoo_sdk.state import EventRecord, LocalStateClient

UTC = timezone.utc
GAP = 3600  # 60-minute fixed gap


def _tmp_db() -> LocalStateClient:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return LocalStateClient(db_path=Path(tmp.name))


def _commit(db, minute, hour=9, day=1, task="101", repo="o/r", source="commit"):
    return db.add_event(
        EventRecord(
            id=None,
            source=source,
            timestamp=datetime(2026, 6, day, hour, minute, tzinfo=UTC),
            task_ids=[task],
            repo=repo,
        )
    )


def _links(db) -> dict:
    return {e.id: e.session_id for e in db.get_events()}


class TestEventConversion(unittest.TestCase):
    def test_record_to_raw_event_maps_source(self):
        rec = EventRecord(
            id=1,
            source="agent",
            timestamp=datetime(2026, 6, 1, 9, tzinfo=UTC),
            task_ids=["1", "2"],
            repo="o/r",
            payload={"pr_title": "t", "pr_body": "b"},
        )
        event = event_record_to_raw_event(rec)
        self.assertEqual(event.event_type, EventType.AGENT)
        self.assertTrue(event.is_release)  # two tasks
        self.assertEqual(event.pr_title, "t")

    def test_unknown_source_raises(self):
        # Unknown sources must fail loudly rather than silently masquerading as
        # commits, which would corrupt sessionization.
        from odoo_sdk.adapters import UnknownEventSourceError

        rec = EventRecord(
            id=1,
            source="mystery",
            timestamp=datetime(2026, 6, 1, 9, tzinfo=UTC),
            task_ids=["1"],
            repo="o/r",
        )
        with self.assertRaises(UnknownEventSourceError):
            event_record_to_raw_event(rec)

    def test_raw_event_to_record_roundtrip(self):
        event = RawEvent(
            timestamp=datetime(2026, 6, 1, 9, tzinfo=UTC),
            task_ids=["101"],
            repo="o/r",
            pr_num=3,
            event_type=EventType.MERGE,
            pr_title="pt",
            pr_body="pb",
        )
        rec = raw_event_to_event_record(event)
        self.assertEqual(rec.source, "merge")
        back = event_record_to_raw_event(rec)
        self.assertEqual(back.event_type, EventType.MERGE)
        self.assertEqual(back.pr_title, "pt")


class TestPersistence(unittest.TestCase):
    def _seed_events(self, db):
        events = [
            RawEvent(
                timestamp=datetime(2026, 6, 1, 9, m, tzinfo=UTC),
                task_ids=["101"],
                repo="o/r",
                pr_num=0,
                event_type=EventType.COMMIT,
            )
            for m in (0, 20, 40)
        ]
        for event in events:
            db.add_event(raw_event_to_event_record(event))

    def test_load_raw_events_reads_back(self):
        db = _tmp_db()
        self._seed_events(db)
        loaded = load_raw_events(db)
        self.assertEqual(len(loaded), 3)
        self.assertTrue(all(isinstance(e, RawEvent) for e in loaded))

    def test_persist_windows_replaces(self):
        db = _tmp_db()
        self._seed_events(db)
        cfg = SessionizationConfig(
            start_date=date(2026, 6, 1), end_date=date(2026, 6, 1)
        )
        events = load_raw_events(db)
        result = transform(events, cfg)
        persist_session_windows(db, result.best_gap_entries)
        first = len(db.get_session_windows())
        self.assertEqual(first, len(result.best_gap_entries))
        # Persisting again with replace should not accumulate duplicates.
        persist_session_windows(db, result.best_gap_entries)
        self.assertEqual(len(db.get_session_windows()), first)

    def test_persist_windows_append(self):
        db = _tmp_db()
        self._seed_events(db)
        cfg = SessionizationConfig(
            start_date=date(2026, 6, 1), end_date=date(2026, 6, 1)
        )
        result = transform(load_raw_events(db), cfg)
        persist_session_windows(db, result.best_gap_entries)
        persist_session_windows(db, result.best_gap_entries, replace=False)
        self.assertEqual(
            len(db.get_session_windows()), 2 * len(result.best_gap_entries)
        )

    def test_time_entry_to_window_fields(self):
        entry = transform(
            [
                RawEvent(
                    timestamp=datetime(2026, 6, 1, 9, tzinfo=UTC),
                    task_ids=["101"],
                    repo="o/r",
                    pr_num=0,
                    event_type=EventType.COMMIT,
                )
            ],
            SessionizationConfig(
                start_date=date(2026, 6, 1), end_date=date(2026, 6, 1)
            ),
        ).best_gap_entries[0]
        window = time_entry_to_session_window(entry)
        self.assertEqual(window.task_id, "101")
        self.assertEqual(window.repo, "o/r")


def _ingest_each(db, records):
    """Ingest each record one at a time (drives the incremental path)."""
    for record in records:
        ingest_events_incrementally(db, [record], GAP)


class TestIncrementalIngest(unittest.TestCase):
    def test_close_events_form_one_session_with_links(self):
        db = _tmp_db()
        recs = [_commit(db, 0), _commit(db, 20), _commit(db, 40)]
        _ingest_each(db, recs)
        windows = db.get_session_windows()
        self.assertEqual(len(windows), 1)
        # Every event links to exactly one (the same) session: no orphan/double.
        links = _links(db)
        self.assertTrue(all(v == windows[0].id for v in links.values()))
        self.assertEqual(len(set(links.values())), 1)

    def test_gap_separates_sessions(self):
        db = _tmp_db()
        _ingest_each(db, [_commit(db, 0), _commit(db, 0, hour=12)])
        self.assertEqual(len(db.get_session_windows()), 2)

    def test_cross_day_is_one_session(self):
        db = _tmp_db()
        a = _commit(db, 30, hour=23, day=1)
        b = _commit(db, 10, hour=0, day=2)  # 40m later, spans midnight
        _ingest_each(db, [a, b])
        windows = db.get_session_windows()
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0].started_at.day, 1)
        self.assertEqual(windows[0].ended_at.day, 2)

    def test_late_event_merges_two_sessions(self):
        db = _tmp_db()
        a = _commit(db, 0, hour=9)
        b = _commit(db, 0, hour=11)
        _ingest_each(db, [a, b])
        self.assertEqual(len(db.get_session_windows()), 2)
        bridge = _commit(db, 0, hour=10)  # 60m from both -> merge
        ingest_events_incrementally(db, [bridge], GAP)
        windows = db.get_session_windows()
        self.assertEqual(len(windows), 1)
        self.assertTrue(all(v == windows[0].id for v in _links(db).values()))

    def test_idempotent_reingest(self):
        db = _tmp_db()
        recs = [_commit(db, 0), _commit(db, 20), _commit(db, 40)]
        _ingest_each(db, recs)
        before_windows = db.get_session_windows()
        before_links = _links(db)
        # Re-ingest everything: no new sessions, identical links.
        ingest_events_incrementally(db, db.get_events(), GAP)
        self.assertEqual(len(db.get_session_windows()), len(before_windows))
        self.assertEqual(_links(db), before_links)

    def test_incremental_equals_full_rebuild(self):
        # Build two DBs from the same events: one incrementally (one at a time),
        # one in a single batch (full rebuild). The session partition matches.
        events_plan = [
            (0, 9, 1), (20, 9, 1), (40, 9, 1),   # session A
            (0, 12, 1), (30, 12, 1),             # session B
            (0, 9, 2),                           # session C (next day)
        ]

        def partition(db):
            return sorted(
                tuple(e.id for e in db.get_events_for_session(w.id))
                for w in db.get_session_windows()
            )

        inc = _tmp_db()
        inc_recs = [_commit(inc, m, hour=h, day=d) for (m, h, d) in events_plan]
        _ingest_each(inc, inc_recs)

        full = _tmp_db()
        full_recs = [_commit(full, m, hour=h, day=d) for (m, h, d) in events_plan]
        ingest_events_incrementally(full, full_recs, GAP)

        # Same number of sessions and same event groupings (ids align 1:1).
        self.assertEqual(len(inc.get_session_windows()), 3)
        self.assertEqual(partition(inc), partition(full))

    def test_agentless_events_use_sentinel_repo(self):
        db = _tmp_db()
        a = _commit(db, 0, repo="", source="agent")
        b = _commit(db, 20, repo="", source="agent")
        _ingest_each(db, [a, b])
        windows = db.get_session_windows()
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0].repo, AGENTLESS_REPO_SENTINEL)

    def test_fixed_strategy_events_are_not_sessionized(self):
        # merge/review are fixed-kind: excluded from incremental sessionization.
        db = _tmp_db()
        m = _commit(db, 0, source="merge")
        created = ingest_events_incrementally(db, [m], GAP)
        self.assertEqual(created, 0)
        self.assertEqual(db.get_session_windows(), [])
        self.assertIsNone(db.get_event(m.id).session_id)

    def test_no_orphans_across_mixed_ingest(self):
        db = _tmp_db()
        recs = [
            _commit(db, 0), _commit(db, 20),
            _commit(db, 0, hour=15),
            _commit(db, 0, day=2, task="202", repo="a/b"),
        ]
        _ingest_each(db, recs)
        # Every commit event is linked (no orphan) to a real session id.
        for event in db.get_events():
            self.assertIsNotNone(event.session_id)
            self.assertIsNotNone(db.get_session_window(event.session_id))


class TestIncrementalPerformanceGuard(unittest.TestCase):
    def test_ingest_cost_independent_of_total_history(self):
        # Seed a large, far-apart history, then time a single-event ingest whose
        # neighborhood is one session. The incremental cost must not grow with
        # total history, so the timed ingest stays fast regardless of history.
        db = _tmp_db()
        base = datetime(2024, 1, 1, 9, tzinfo=UTC)
        history = 400
        for i in range(history):
            # Each event a full day apart -> its own isolated session.
            ts = base + timedelta(days=i)
            rec = db.add_event(
                EventRecord(
                    id=None, source="commit", timestamp=ts,
                    task_ids=["101"], repo="o/r",
                )
            )
            ingest_events_incrementally(db, [rec], GAP)
        self.assertEqual(len(db.get_session_windows()), history)

        # A new event adjacent to only the last session touches one neighborhood.
        near = db.add_event(
            EventRecord(
                id=None, source="commit",
                timestamp=base + timedelta(days=history - 1, minutes=20),
                task_ids=["101"], repo="o/r",
            )
        )
        t0 = time.perf_counter()
        ingest_events_incrementally(db, [near], GAP)
        elapsed = time.perf_counter() - t0
        # One neighborhood ingest is cheap even against a large history.
        self.assertLess(elapsed, 0.25)
        self.assertEqual(len(db.get_session_windows()), history)  # merged, no new


if __name__ == "__main__":
    unittest.main()
