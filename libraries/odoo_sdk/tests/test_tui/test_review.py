"""Tests for the TUI review surface: lines + transitions (#378 items 7-9).

Covers the pure ``review_body_lines`` composer and drives the real transition
path end to end over a fixture central DB and a mocked Odoo transport: a session
whose task/day already carries manual Odoo lines shows the already-logged partial
badge; two cross-task sessions overlapping 40m badge each other; a single-event
weak and a multi-event strong session classify correctly; the evidence pane lists
the member-event citations; and an offline transport still renders, just without
the logged badge. Everything here only informs the reviewer — no transition trims
or uploads.
"""

import unittest
from datetime import date, datetime, timezone

from odoo_sdk.state import EventRecord
from odoo_sdk.tui.app import (
    AppState,
    TuiDeps,
    enter_review,
    exit_review,
    move_review_selection,
    toggle_evidence,
)
from odoo_sdk.tui.evidence import STRONG, WEAK, Overlap, ReviewCard
from odoo_sdk.tui.review import review_body_lines
from odoo_sdk.tui.window import DateWindow
from tests.support import make_state_db

UTC = timezone.utc


# ── Fake registry plumbing ──────────────────────────────────────────────────


class FakeCommand:
    def __init__(self, result=None, state=None, client=None):
        self._result = result
        self.state = state
        self._client = client
        self.calls = []

    def execute(self, **kwargs):
        self.calls.append(kwargs)
        return self._result


class FakeRegistry:
    def __init__(self, commands):
        self._commands = commands

    def __getitem__(self, name):
        return self._commands[name]


class FakeClient:
    """Read-only transport returning canned logged lines (or raising, offline)."""

    def __init__(self, lines=None, raise_on=None):
        self.uid = 7
        self._lines = lines or []
        self._raise_on = raise_on

    def execute(self, model, method, *args, **kwargs):
        if self._raise_on is not None:
            raise self._raise_on
        if model == "hr.employee":
            return [{"id": 42}]
        return self._lines


def _add(store, source, ts, external_id, payload=None, task="24648", repo="acme/web"):
    record = store.add_event(
        EventRecord(
            id=None,
            source=source,
            timestamp=ts,
            task_ids=[task],
            repo=repo,
            payload=payload,
            external_id=external_id,
        )
    )
    return record.id


def _session(sid, task, start, end, event_ids):
    return {
        "session_id": sid,
        "task_id": task,
        "started_at": start.isoformat(),
        "ended_at": end.isoformat(),
        "duration_secs": (end - start).total_seconds(),
        "events": [{"event_id": eid} for eid in event_ids],
    }


def _fixture():
    """Build a store + four session dicts exercising every review signal.

    S1 (24648): 3h, two direct events → STRONG, and 2h already logged → partial.
    S2 (55555): single chatter event → WEAK.
    S3 (70001) / S4 (70002): overlap 40m on the next day → both WEAK, badge peers.
    """
    store = make_state_db()
    d1 = datetime(2026, 7, 1, tzinfo=UTC)
    d2 = datetime(2026, 7, 2, tzinfo=UTC)
    e_commit = _add(store, "commit", d1.replace(hour=9), "git:deadbeefcafe0", task="24648")
    e_merge = _add(store, "merge", d1.replace(hour=11), "gh:pr:189", task="24648")
    e_chat = _add(store, "chatter", d1.replace(hour=13), "odoo:mail:900", task="55555")
    e_o1a = _add(store, "commit", d2.replace(hour=9), "git:aaa1111", task="70001")
    e_o1b = _add(store, "commit", d2.replace(hour=9, minute=30), "git:aaa2222", task="70001")
    e_o2 = _add(store, "commit", d2.replace(hour=9, minute=20), "git:bbb3333", task="70002")
    sessions = [
        _session(1, "24648", d1.replace(hour=9), d1.replace(hour=12), [e_commit, e_merge]),
        _session(2, "55555", d1.replace(hour=13), d1.replace(hour=13, minute=30), [e_chat]),
        _session(3, "70001", d2.replace(hour=9), d2.replace(hour=10), [e_o1a, e_o1b]),
        _session(4, "70002", d2.replace(hour=9, minute=20), d2.replace(hour=11), [e_o2]),
    ]
    return store, sessions


def _deps(store, client):
    """Bundle the driver's injected deps: the store and the read-only RPC client.

    The review surface reads member events off the injected store and best-effort
    reads already-logged hours off the injected client — never harvested off a
    command instance's ``.state`` or private ``._client``.
    """
    registry = FakeRegistry({"query_sessions": FakeCommand(state=store)})
    return TuiDeps(registry=registry, client=client, store=store, config=None)


def _state(store, sessions):
    return AppState(
        window=DateWindow(date(2026, 7, 1), date(2026, 7, 2)), sessions=sessions
    )


# ── Line composition ────────────────────────────────────────────────────────


def _cards():
    return [
        ReviewCard(1, "24648", "2026-07-01T09:00:00", "2026-07-01T12:00:00", 3.0,
                   STRONG, 2.0, "partial", (), ("commit deadbee", "PR #189"), False),
        ReviewCard(2, "55555", "2026-07-01T13:00:00", "2026-07-01T13:30:00", 0.5,
                   WEAK, 0.0, "", (Overlap("70002", 40),), ("chatter msg 900",), False),
    ]


class TestReviewBodyLines(unittest.TestCase):
    def test_one_line_per_card_when_collapsed(self):
        lines = review_body_lines(_cards(), 0, False)
        self.assertEqual(len(lines), 2)

    def test_card_shows_task_confidence_and_logged_badge(self):
        body = "\n".join(review_body_lines(_cards(), 0, False))
        self.assertIn("task 24648", body)
        self.assertIn("[STRONG]", body)
        self.assertIn("logged 2.0h (partial)", body)

    def test_selected_marker_and_overlap_badge(self):
        body = "\n".join(review_body_lines(_cards(), 1, False))
        self.assertIn("> task 55555", body)
        self.assertIn("[WEAK]", body)
        self.assertIn("overlaps task 70002 by 40m", body)

    def test_evidence_pane_hidden_until_expanded(self):
        collapsed = "\n".join(review_body_lines(_cards(), 0, False))
        self.assertNotIn("PR #189", collapsed)
        expanded = "\n".join(review_body_lines(_cards(), 0, True))
        self.assertIn("evidence — task 24648", expanded)
        self.assertIn("commit deadbee", expanded)
        self.assertIn("PR #189", expanded)

    def test_pane_shows_logged_detail(self):
        body = "\n".join(review_body_lines(_cards(), 0, True))
        self.assertIn("already logged 2.00h on this task today (partial overlap)", body)

    def test_empty_message(self):
        lines = review_body_lines([], 0, False)
        self.assertIn("no sessions in window to review", "\n".join(lines))

    def test_multi_overlap_collapses_to_count(self):
        card = ReviewCard(1, "24648", "2026-07-01T09:00:00", "2026-07-01T12:00:00",
                          3.0, WEAK, 0.0, "", (Overlap("a", 10), Overlap("b", 20)),
                          (), False)
        body = "\n".join(review_body_lines([card], 0, False))
        self.assertIn("overlaps 2 sessions", body)

    def test_overlap_detail_listed_in_pane(self):
        card = ReviewCard(1, "24648", "2026-07-01T09:00:00", "2026-07-01T12:00:00",
                          3.0, WEAK, 0.0, "", (Overlap("70002", 40),),
                          ("commit abc1234",), False)
        body = "\n".join(review_body_lines([card], 0, True))
        self.assertIn("overlaps task 70002 by 40m", body)

    def test_unvalidated_shown_in_pane(self):
        card = ReviewCard(1, "999", "2026-07-01T09:00:00", "2026-07-01T10:00:00",
                          1.0, WEAK, 0.0, "", (), ("commit abc1234",), True)
        body = "\n".join(review_body_lines([card], 0, True))
        self.assertIn("task id unvalidated", body)

    def test_pane_notes_no_linked_events(self):
        card = ReviewCard(1, "999", "2026-07-01T09:00:00", "2026-07-01T10:00:00",
                          1.0, WEAK, 0.0, "", (), (), False)
        body = "\n".join(review_body_lines([card], 0, True))
        self.assertIn("no linked events", body)


# ── Entering review and the badges it computes ──────────────────────────────


class TestEnterReview(unittest.TestCase):
    def test_builds_cards_with_all_signals(self):
        store, sessions = _fixture()
        client = FakeClient(
            lines=[{"task_id": [24648, "T"], "date": "2026-07-01", "unit_amount": 2.0}]
        )
        state = enter_review(_deps(store, client), _state(store, sessions))
        self.assertEqual(state.mode, "review")
        by_task = {c.task_id: c for c in state.review_cards}
        # Item 9: strong (validated id, two direct events, no overlap).
        self.assertEqual(by_task["24648"].confidence, STRONG)
        self.assertEqual(by_task["24648"].citations, ("commit deadbee", "PR #189"))
        # Item 7: 2h already logged on a 3h session → partial badge.
        self.assertEqual(by_task["24648"].logged_hours, 2.0)
        self.assertEqual(by_task["24648"].logged_flag, "partial")
        # Item 9: single-event session is weak.
        self.assertEqual(by_task["55555"].confidence, WEAK)
        # Item 8: the two next-day sessions overlap 40m and badge each other.
        self.assertEqual(by_task["70001"].overlaps, (Overlap("70002", 40),))
        self.assertEqual(by_task["70002"].overlaps, (Overlap("70001", 40),))
        self.assertEqual(by_task["70001"].confidence, WEAK)  # overlap → weak

    def test_offline_transport_still_renders_without_logged_badge(self):
        store, sessions = _fixture()
        client = FakeClient(raise_on=RuntimeError("odoo unreachable"))
        state = enter_review(_deps(store, client), _state(store, sessions))
        self.assertEqual(state.mode, "review")
        by_task = {c.task_id: c for c in state.review_cards}
        # No badge offline, but the card (and its overlap/confidence) still render.
        self.assertEqual(by_task["24648"].logged_flag, "")
        self.assertEqual(by_task["24648"].logged_hours, 0.0)
        self.assertEqual(by_task["24648"].confidence, STRONG)


# ── Transitions ─────────────────────────────────────────────────────────────


class TestReviewTransitionsOverFixture(unittest.TestCase):
    def _opened(self):
        store, sessions = _fixture()
        deps = _deps(store, FakeClient())
        state = enter_review(deps, _state(store, sessions))
        return deps, state

    def test_down_up_move_selection_and_collapse_pane(self):
        _, state = self._opened()
        state = move_review_selection(state, 1)
        self.assertEqual(state.review_selected, 1)
        state = toggle_evidence(state)  # open pane
        self.assertTrue(state.review_expanded)
        state = move_review_selection(state, -1)  # move collapses it
        self.assertEqual(state.review_selected, 0)
        self.assertFalse(state.review_expanded)

    def test_toggle_flips_evidence(self):
        _, state = self._opened()
        state = toggle_evidence(state)
        self.assertTrue(state.review_expanded)
        state = toggle_evidence(state)
        self.assertFalse(state.review_expanded)

    def test_exit_returns_to_main(self):
        _, state = self._opened()
        state = exit_review(state)
        self.assertEqual(state.mode, "main")
        self.assertFalse(state.review_expanded)

    def test_up_clamps_at_top(self):
        _, state = self._opened()
        state = move_review_selection(state, -1)
        self.assertEqual(state.review_selected, 0)

    def test_move_and_toggle_on_empty_cards_are_noops(self):
        state = AppState(
            window=DateWindow(date(2026, 7, 1), date(2026, 7, 2)),
            sessions=[],
            mode="review",
            review_cards=[],
        )
        self.assertEqual(move_review_selection(state, 1), state)
        self.assertEqual(toggle_evidence(state), state)


if __name__ == "__main__":
    unittest.main()
