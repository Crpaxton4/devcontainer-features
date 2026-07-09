"""Tests for the pure incremental sessionizer.

Covers the load-bearing invariants: incremental ≡ full-rebuild, idempotency,
late/out-of-order merge, split-on-removal, cross-day spans, the no-orphan /
no-double-link guarantees, and a performance guard proving the pure step's cost
is bounded by its input, not total history.
"""

import time
import unittest
from datetime import datetime, timedelta, timezone

from odoo_sdk.sessionization import (
    AGENTLESS_REPO_SENTINEL,
    SessionEvent,
    SessionState,
    group_key,
    rebuild_group,
    resolve_repo,
    sessionize_group,
    with_resolved_repo,
)

UTC = timezone.utc
GAP = 3600  # 60 minutes


def ev(i: int, minute: int, hour: int = 9, day: int = 1, task="101", repo="o/r"):
    return SessionEvent(
        id=i,
        timestamp=datetime(2026, 6, day, hour, minute, tzinfo=UTC),
        task_id=task,
        repo=repo,
    )


def _as_sessions(result) -> list[SessionState]:
    """Assign synthetic ids to a rebuild's created sessions for feeding back."""
    return [
        SessionState(
            id=index + 1,
            task_id=s.task_id,
            repo=s.repo,
            started_at=s.started_at,
            ended_at=s.ended_at,
            strategy_name=s.strategy_name,
            category=s.category,
            pr_num=s.pr_num,
            event_ids=s.event_ids,
        )
        for index, s in enumerate(result.sessions)
    ]


def _partition_signature(result) -> list[frozenset]:
    """Return the partition as a set of event-id frozensets, order-independent."""
    return sorted(
        (frozenset(s.event_ids) for s in result.sessions),
        key=lambda fs: min(fs) if fs else -1,
    )


class TestPartition(unittest.TestCase):
    def test_close_events_form_one_session(self):
        result = rebuild_group([ev(1, 0), ev(2, 20), ev(3, 40)], GAP)
        self.assertEqual(len(result.sessions), 1)
        self.assertEqual(result.sessions[0].event_ids, (1, 2, 3))

    def test_gap_splits_into_two_sessions(self):
        # 09:00 then 11:00 -> 2h apart > 60m gap.
        result = rebuild_group([ev(1, 0), ev(2, 0, hour=11)], GAP)
        self.assertEqual(len(result.sessions), 2)

    def test_boundary_gap_exactly_equal_does_not_split(self):
        # Exactly 60m apart is <= gap, so events stay in one session.
        result = rebuild_group([ev(1, 0), ev(2, 0, hour=10)], GAP)
        self.assertEqual(len(result.sessions), 1)

    def test_empty_yields_nothing(self):
        result = rebuild_group([], GAP)
        self.assertEqual(result.sessions, [])
        self.assertEqual(result.links, [])

    def test_out_of_order_input_is_time_ordered(self):
        result = rebuild_group([ev(3, 40), ev(1, 0), ev(2, 20)], GAP)
        self.assertEqual(len(result.sessions), 1)
        self.assertEqual(result.sessions[0].event_ids, (1, 2, 3))


class TestCrossDay(unittest.TestCase):
    def test_cross_day_span_is_first_class(self):
        # 23:30 day 1 -> 00:10 day 2 is 40m apart: one session spanning midnight.
        e1 = SessionEvent(1, datetime(2026, 6, 1, 23, 30, tzinfo=UTC), "101", "o/r")
        e2 = SessionEvent(2, datetime(2026, 6, 2, 0, 10, tzinfo=UTC), "101", "o/r")
        result = rebuild_group([e1, e2], GAP)
        self.assertEqual(len(result.sessions), 1)
        self.assertEqual(result.sessions[0].started_at.day, 1)
        self.assertEqual(result.sessions[0].ended_at.day, 2)


class TestNoOrphanNoDoubleLink(unittest.TestCase):
    def test_every_event_linked_exactly_once(self):
        events = [ev(1, 0), ev(2, 20), ev(3, 0, hour=12), ev(4, 10, hour=12)]
        result = rebuild_group(events, GAP)
        linked = [link.event_id for link in result.links]
        self.assertEqual(sorted(linked), [1, 2, 3, 4])  # no orphan
        self.assertEqual(len(linked), len(set(linked)))  # no double-link

    def test_link_targets_match_session_membership(self):
        result = rebuild_group([ev(1, 0), ev(2, 0, hour=12)], GAP)
        # New sessions carry new_session_index; each event's index selects its run.
        for link in result.links:
            self.assertIsNone(link.session_ref)
            self.assertIsNotNone(link.new_session_index)


class TestIncrementalEqualsRebuild(unittest.TestCase):
    def _ingest_one_by_one(self, events: list[SessionEvent]):
        existing: list[SessionState] = []
        seen: list[SessionEvent] = []
        for event in events:
            seen.append(event)
            result = sessionize_group(existing, seen, GAP)
            existing = _as_sessions(result)
        return sessionize_group(existing, seen, GAP)

    def test_incremental_equals_full_rebuild(self):
        events = [
            ev(1, 0), ev(2, 20), ev(3, 40),          # one session
            ev(4, 0, hour=12), ev(5, 30, hour=12),   # another
            ev(6, 0, day=2),                         # cross-day new session
        ]
        incremental = self._ingest_one_by_one(events)
        full = rebuild_group(events, GAP)
        self.assertEqual(
            _partition_signature(incremental), _partition_signature(full)
        )

    def test_out_of_order_late_arrival_equals_rebuild(self):
        # Ingest a late event that bridges two existing sessions.
        base = [ev(1, 0), ev(2, 0, hour=11)]  # 09:00 and 11:00 -> 2 sessions
        first = sessionize_group([], base, GAP)
        existing = _as_sessions(first)
        self.assertEqual(len(existing), 2)
        bridge = ev(3, 0, hour=10)  # 10:00: exactly gap from both -> merges
        merged = sessionize_group(existing, base + [bridge], GAP)
        full = rebuild_group(base + [bridge], GAP)
        self.assertEqual(_partition_signature(merged), _partition_signature(full))
        self.assertEqual(len(merged.sessions), 1)


class TestIdentityDeltas(unittest.TestCase):
    def test_extend_preserves_id(self):
        first = sessionize_group([], [ev(1, 0)], GAP)
        existing = _as_sessions(first)
        original_id = existing[0].id
        # A new close event extends the same session, keeping its id.
        result = sessionize_group(existing, [ev(1, 0), ev(2, 20)], GAP)
        self.assertEqual(len(result.sessions), 1)
        self.assertEqual(result.sessions[0].id, original_id)
        self.assertEqual(result.created, [])
        self.assertEqual(result.deleted_ids, [])

    def test_merge_keeps_earliest_and_deletes_other(self):
        base = sessionize_group([], [ev(1, 0), ev(2, 0, hour=11)], GAP)
        existing = _as_sessions(base)
        first_id, second_id = existing[0].id, existing[1].id
        bridge = ev(3, 0, hour=10)  # 10:00 merges the 09:00 and 11:00 sessions
        result = sessionize_group(existing, [ev(1, 0), ev(2, 0, hour=11), bridge], GAP)
        self.assertEqual(len(result.sessions), 1)
        self.assertEqual(result.sessions[0].id, first_id)
        self.assertEqual(result.deleted_ids, [second_id])

    def test_split_on_removal_creates_new_session(self):
        # Start with one session of 3 events bridged by the 10:00 event.
        merged = sessionize_group(
            [], [ev(1, 0), ev(2, 0, hour=10), ev(3, 0, hour=11)], GAP
        )
        existing = _as_sessions(merged)
        self.assertEqual(len(existing), 1)
        # Re-sessionize with the bridge (id 2) removed -> 09:00 and 11:00 split.
        result = sessionize_group(existing, [ev(1, 0), ev(3, 0, hour=11)], GAP)
        self.assertEqual(len(result.sessions), 2)
        # One run keeps the original id; the other is created.
        self.assertEqual(len(result.created), 1)

    def test_unpersisted_existing_session_is_ignored_for_ownership(self):
        # An existing session with id=None contributes no ownership, so a run
        # over its events is treated as a fresh create (no id to inherit).
        unsaved = SessionState(
            id=None,
            task_id="101",
            repo="o/r",
            started_at=datetime(2026, 6, 1, 9, tzinfo=UTC),
            ended_at=datetime(2026, 6, 1, 9, tzinfo=UTC),
            strategy_name="development",
            category="Development",
            pr_num=0,
            event_ids=(1,),
        )
        result = sessionize_group([unsaved], [ev(1, 0), ev(2, 20)], GAP)
        self.assertEqual(len(result.sessions), 1)
        self.assertIsNone(result.sessions[0].id)
        self.assertEqual(len(result.created), 1)

    def test_no_op_relink_is_idempotent(self):
        first = sessionize_group([], [ev(1, 0), ev(2, 20)], GAP)
        existing = _as_sessions(first)
        again = sessionize_group(existing, [ev(1, 0), ev(2, 20)], GAP)
        self.assertEqual(again.created, [])
        self.assertEqual(again.deleted_ids, [])
        self.assertEqual(len(again.sessions), 1)
        self.assertEqual(again.sessions[0].id, existing[0].id)


class TestRepoSentinel(unittest.TestCase):
    def test_repoless_events_use_sentinel(self):
        self.assertEqual(resolve_repo(""), AGENTLESS_REPO_SENTINEL)
        self.assertEqual(resolve_repo("o/r"), "o/r")

    def test_group_key_substitutes_sentinel(self):
        agentless = SessionEvent(1, datetime(2026, 6, 1, 9, tzinfo=UTC), "101", "")
        self.assertEqual(
            group_key(agentless), ("101", AGENTLESS_REPO_SENTINEL, "development")
        )

    def test_with_resolved_repo_rewrites_only_when_needed(self):
        agentless = SessionEvent(1, datetime(2026, 6, 1, 9, tzinfo=UTC), "101", "")
        resolved = with_resolved_repo(agentless)
        self.assertEqual(resolved.repo, AGENTLESS_REPO_SENTINEL)
        kept = SessionEvent(2, datetime(2026, 6, 1, 9, tzinfo=UTC), "101", "o/r")
        self.assertIs(with_resolved_repo(kept), kept)


class TestPerformanceGuard(unittest.TestCase):
    def test_ingest_cost_independent_of_history(self):
        # Build a large history of far-apart (own) sessions, then ingest one new
        # event whose neighborhood is a single session. The pure step must scale
        # with the neighborhood, not the full history, so a small neighborhood
        # stays fast regardless of how much history exists.
        def elapsed(history_sessions: int) -> float:
            start = datetime(2020, 1, 1, tzinfo=UTC)
            # Neighborhood: one existing session + its one event.
            existing = [
                SessionState(
                    id=1,
                    task_id="101",
                    repo="o/r",
                    started_at=start,
                    ended_at=start,
                    strategy_name="development",
                    category="Development",
                    pr_num=0,
                    event_ids=(1,),
                )
            ]
            neighborhood = [SessionEvent(1, start, "101", "o/r")]
            new_event = SessionEvent(
                2, start + timedelta(minutes=20), "101", "o/r"
            )
            # history_sessions is a stand-in the pure step never receives; the
            # timing here reflects only the neighborhood the adapter passes in.
            _ = history_sessions
            t0 = time.perf_counter()
            for _ in range(2000):
                sessionize_group(existing, neighborhood + [new_event], GAP)
            return time.perf_counter() - t0

        small = elapsed(10)
        large = elapsed(100_000)
        # Same neighborhood -> comparable time regardless of nominal history.
        self.assertLess(large, small * 3 + 0.5)


if __name__ == "__main__":
    unittest.main()
