"""Tests for the review-surface evidence computation (#378 items 7-9).

Pure functions: cross-task overlap (item 8), citation extraction and confidence
classification (item 9), the already-logged flag (item 7), and card assembly.
No terminal, no live Odoo — the impure fetches happen in the driver.
"""

import unittest
from datetime import datetime, timezone

from odoo_sdk.state import EventRecord
from odoo_sdk.tui.evidence import (
    STRONG,
    WEAK,
    Overlap,
    build_review_cards,
    classify_confidence,
    compute_overlaps,
    event_citation,
    event_unvalidated,
    logged_flag,
    overlap_seconds,
)

UTC = timezone.utc


def _session(sid, task_id, start, end, events=None):
    started = datetime(2026, 7, 1, *start, tzinfo=UTC)
    ended = datetime(2026, 7, 1, *end, tzinfo=UTC)
    return {
        "session_id": sid,
        "task_id": task_id,
        "started_at": started.isoformat(),
        "ended_at": ended.isoformat(),
        "duration_secs": (ended - started).total_seconds(),
        "events": events or [],
    }


def _event(event_id, source="commit", external_id=None, payload=None):
    return EventRecord(
        id=event_id,
        source=source,
        timestamp=datetime(2026, 7, 1, 9, 0, tzinfo=UTC),
        task_ids=["24648"],
        repo="acme/web",
        payload=payload,
        external_id=external_id,
    )


# ── Overlap (item 8) ────────────────────────────────────────────────────────


class TestOverlap(unittest.TestCase):
    def test_overlap_seconds_disjoint_is_zero(self):
        a0 = datetime(2026, 7, 1, 9, tzinfo=UTC)
        a1 = datetime(2026, 7, 1, 10, tzinfo=UTC)
        b0 = datetime(2026, 7, 1, 10, tzinfo=UTC)
        b1 = datetime(2026, 7, 1, 11, tzinfo=UTC)
        self.assertEqual(overlap_seconds(a0, a1, b0, b1), 0.0)

    def test_cross_task_overlap_badges_both_by_minutes(self):
        sessions = [
            _session(1, "24648", (9, 0), (10, 0)),
            _session(2, "31000", (9, 20), (11, 0)),  # overlaps 1 by 40m
        ]
        overlaps = compute_overlaps(sessions)
        self.assertEqual(overlaps[1], (Overlap("31000", 40),))
        self.assertEqual(overlaps[2], (Overlap("24648", 40),))

    def test_same_task_never_overlaps_itself(self):
        sessions = [
            _session(1, "24648", (9, 0), (10, 0)),
            _session(2, "24648", (9, 30), (10, 30)),
        ]
        self.assertEqual(compute_overlaps(sessions), {})

    def test_sub_minute_brush_is_not_an_overlap(self):
        # A 30-second touch is below the one-minute floor, so it never badges
        # (and would otherwise round to a meaningless "by 0m").
        sessions = [
            _session(1, "24648", (9, 0), (10, 0)),
            {
                **_session(2, "31000", (9, 59), (11, 0)),
                "started_at": datetime(2026, 7, 1, 9, 59, 30, tzinfo=UTC).isoformat(),
            },
        ]
        self.assertEqual(compute_overlaps(sessions), {})


# ── Citations (item 9) ──────────────────────────────────────────────────────


class TestCitations(unittest.TestCase):
    def test_commit_sha_is_shortened(self):
        self.assertEqual(event_citation("commit", "git:abcdef1234567890"), "commit abcdef1")

    def test_pr_number(self):
        self.assertEqual(event_citation("merge", "gh:pr:189"), "PR #189")

    def test_review_comment_chatter(self):
        self.assertEqual(event_citation("review", "gh:review:551"), "review 551")
        self.assertEqual(event_citation("comment", "gh:comment:77"), "comment 77")
        self.assertEqual(event_citation("chatter", "odoo:mail:900"), "chatter msg 900")

    def test_unknown_external_id_passes_through(self):
        self.assertEqual(event_citation("agent", "custom:xyz"), "custom:xyz")

    def test_no_external_id_falls_back_to_source(self):
        self.assertEqual(event_citation("agent", None), "agent")


# ── Unvalidated flag (item 9, defensive #378a read) ─────────────────────────


class TestUnvalidatedFlag(unittest.TestCase):
    def test_none_or_non_dict_payload_is_no_signal(self):
        self.assertFalse(event_unvalidated(None))
        self.assertFalse(event_unvalidated("nope"))

    def test_absent_key_is_no_signal(self):
        self.assertFalse(event_unvalidated({"other": True}))

    def test_truthy_list_marks_unvalidated(self):
        self.assertTrue(event_unvalidated({"unvalidated_task_ids": ["999"]}))

    def test_empty_list_is_not_unvalidated(self):
        self.assertFalse(event_unvalidated({"unvalidated_task_ids": []}))

    def test_other_key_spellings_are_no_signal(self):
        self.assertFalse(event_unvalidated({"task_ids_unvalidated": True}))
        self.assertFalse(event_unvalidated({"unvalidated": True}))


# ── Confidence (item 9) ─────────────────────────────────────────────────────


class TestConfidence(unittest.TestCase):
    def test_strong_needs_valid_id_multiple_events_no_overlap(self):
        self.assertEqual(classify_confidence("24648", 3, False, False), STRONG)

    def test_single_event_is_weak(self):
        self.assertEqual(classify_confidence("24648", 1, False, False), WEAK)

    def test_overlap_is_weak(self):
        self.assertEqual(classify_confidence("24648", 3, True, False), WEAK)

    def test_unvalidated_is_weak(self):
        self.assertEqual(classify_confidence("24648", 3, False, True), WEAK)

    def test_non_numeric_task_id_is_weak(self):
        self.assertEqual(classify_confidence("", 3, False, False), WEAK)
        self.assertEqual(classify_confidence("0", 3, False, False), WEAK)


# ── Logged flag (item 7) ────────────────────────────────────────────────────


class TestLoggedFlag(unittest.TestCase):
    def test_nothing_logged(self):
        self.assertEqual(logged_flag(1.0, 0.0), "")

    def test_partial(self):
        self.assertEqual(logged_flag(2.0, 0.5), "partial")

    def test_full_when_logged_meets_or_exceeds(self):
        self.assertEqual(logged_flag(1.0, 1.0), "full")
        self.assertEqual(logged_flag(1.0, 3.0), "full")


# ── Card assembly ───────────────────────────────────────────────────────────


class TestBuildReviewCards(unittest.TestCase):
    def test_strong_card_with_citations_and_partial_logged(self):
        session = _session(
            1, "24648", (9, 0), (11, 0), events=[{"event_id": 10}, {"event_id": 11}]
        )
        events = {
            1: [
                _event(10, source="commit", external_id="git:deadbeefcafe"),
                _event(11, source="merge", external_id="gh:pr:189"),
            ]
        }
        cards = build_review_cards(
            [session], events, {("24648", "2026-07-01"): 0.5}, {}
        )
        card = cards[0]
        self.assertEqual(card.confidence, STRONG)
        self.assertEqual(card.citations, ("commit deadbee", "PR #189"))
        self.assertEqual(card.logged_hours, 0.5)
        self.assertEqual(card.logged_flag, "partial")
        self.assertFalse(card.unvalidated)

    def test_weak_single_event_no_logged(self):
        session = _session(1, "24648", (9, 0), (10, 0), events=[{"event_id": 10}])
        cards = build_review_cards(
            [session], {1: [_event(10)]}, {}, {}
        )
        self.assertEqual(cards[0].confidence, WEAK)
        self.assertEqual(cards[0].logged_flag, "")

    def test_unvalidated_event_marks_card_weak(self):
        session = _session(
            1, "24648", (9, 0), (11, 0), events=[{"event_id": 10}, {"event_id": 11}]
        )
        events = {
            1: [
                _event(10, external_id="git:aaa111"),
                _event(11, external_id="git:bbb222", payload={"unvalidated_task_ids": ["99999"]}),
            ]
        }
        cards = build_review_cards([session], events, {}, {})
        self.assertTrue(cards[0].unvalidated)
        self.assertEqual(cards[0].confidence, WEAK)

    def test_overlap_carried_and_forces_weak(self):
        session = _session(
            1, "24648", (9, 0), (11, 0), events=[{"event_id": 10}, {"event_id": 11}]
        )
        overlaps = {1: (Overlap("31000", 40),)}
        cards = build_review_cards(
            [session], {1: [_event(10), _event(11)]}, {}, overlaps
        )
        self.assertEqual(cards[0].overlaps, (Overlap("31000", 40),))
        self.assertEqual(cards[0].confidence, WEAK)

    def test_missing_member_events_degrade_to_empty_trail(self):
        session = _session(1, "24648", (9, 0), (10, 0))
        cards = build_review_cards([session], {}, {}, {})
        self.assertEqual(cards[0].citations, ())
        self.assertEqual(cards[0].confidence, WEAK)  # zero events -> weak


if __name__ == "__main__":
    unittest.main()
