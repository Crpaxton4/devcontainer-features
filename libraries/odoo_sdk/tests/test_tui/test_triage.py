"""Tests for the TUI triage queue (issue #370, acceptance item 9).

Covers the pure series-grouping and line composition, and drives the real
transition path end to end: a fixture central DB holding a 13-tick calendar
series (one row) plus a lone unattributed event, opened via triage, assigned to a
task in one transaction, and then re-derived as a billable session.

Every tick external id here comes from the REAL ingestion producer
(``_tick_external_id``/``_expand_ticks``) rather than a fabricated
``:tick:<index>`` string. Fabricated numeric ids collapse to one row under any
regex and so hid a producer/consumer format mismatch (issues #517, #526); driving
the tests through the producer makes this file the contract test for that seam.
"""

import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from odoo_sdk.adapters.external_sync import _expand_ticks, _tick_external_id
from odoo_sdk.commands.builtin import AssignEventCommand
from odoo_sdk.state import EventRecord
from odoo_sdk.tui.app import (
    AppState,
    TuiDeps,
    assign_triage,
    enter_triage,
    erase_triage_digit,
    exit_triage,
    move_triage_selection,
    type_triage_digit,
)
from odoo_sdk.tui.triage import (
    TriageRow,
    build_triage_rows,
    row_line,
    series_key,
    triage_body_lines,
)
from odoo_sdk.tui.window import DateWindow
from tests.support import make_state_db

UTC = timezone.utc

# A real 09:00–10:00 meeting on the ingestion tick interval: 13 ticks.
MEETING_START = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
MEETING_END = MEETING_START + timedelta(hours=1)
TICK_MINS = 5


def _tick_moments(start=MEETING_START, end=MEETING_END, tick_mins=TICK_MINS):
    """Return the tick timestamps ingestion expands a meeting into."""
    return _expand_ticks(start, end, tick_mins)


def _tick_ids(series_id="gcal:evt-9", **kwargs):
    """Return the tick external ids ingestion writes for one meeting.

    Built by the production producer, so the keys under test are byte-identical
    to what the calendar resync stores.
    """
    return [_tick_external_id(series_id, moment) for moment in _tick_moments(**kwargs)]


def _rec(event_id, *, source="chatter", ts=None, subject="", external_id=None):
    return EventRecord(
        id=event_id,
        source=source,
        timestamp=ts or datetime(2026, 6, 1, 9, 0, tzinfo=UTC),
        task_ids=[],
        repo="",
        subject=subject,
        external_id=external_id,
    )


class FakeCommand:
    def __init__(self, result=None, state=None):
        self._result = result
        self.state = state
        self.calls = []

    def execute(self, **kwargs):
        self.calls.append(kwargs)
        return self._result


class FakeRegistry:
    def __init__(self, commands):
        self._commands = commands

    def __getitem__(self, name):
        return self._commands[name]


def _deps(store):
    """Bundle the driver's injected deps over a real store.

    The triage write is routed through the real ``assign_event`` command bound to
    the same store, so the assignment lands in the DB exactly as it does in
    production (no fake short-circuits the write path).
    """
    registry = FakeRegistry(
        {
            "query_sessions": FakeCommand(state=store),
            "assign_event": AssignEventCommand(None, state=store),
        }
    )
    return TuiDeps(registry=registry, client=None, store=store, config=None)


# ── Series recognition ──────────────────────────────────────────────────────


class TestSeriesKey(unittest.TestCase):
    def test_tick_member_yields_shared_prefix(self):
        tick = _tick_external_id("gcal:evt-9", MEETING_START)
        self.assertEqual(tick, "gcal:evt-9:tick:2026-06-01T09:00:00+00:00")
        self.assertEqual(series_key(tick), "gcal:evt-9:tick:")

    def test_every_tick_of_one_event_shares_a_key(self):
        ids = _tick_ids()
        self.assertEqual(len(ids), 13)
        self.assertEqual({series_key(tick) for tick in ids}, {"gcal:evt-9:tick:"})

    def test_naive_and_offset_moments_are_recognized(self):
        # The producer normalizes to UTC, so a naive moment still yields a tick
        # id the consumer parses — no silent split into 13 lone rows.
        east = timezone(timedelta(hours=2))
        naive = _tick_external_id("gcal:evt-9", datetime(2026, 6, 1, 9, 0))
        shifted = _tick_external_id(
            "gcal:evt-9", datetime(2026, 6, 1, 11, 0, tzinfo=east)
        )
        self.assertEqual(series_key(naive), "gcal:evt-9:tick:")
        self.assertEqual(series_key(shifted), "gcal:evt-9:tick:")

    def test_sub_second_moment_is_recognized(self):
        moment = MEETING_START + timedelta(microseconds=250)
        tick = _tick_external_id("gcal:evt-9", moment)
        self.assertEqual(series_key(tick), "gcal:evt-9:tick:")

    def test_non_tick_external_id_is_not_a_series(self):
        self.assertIsNone(series_key("gcal:evt-9"))
        self.assertIsNone(series_key("gh:pr:42"))

    def test_non_timestamp_tick_suffix_is_not_a_series(self):
        # The ISO-timestamp suffix is the canonical form; ingestion never emits a
        # bare index, so an index-shaped suffix must not be read as a series.
        self.assertIsNone(series_key("gcal:evt-9:tick:7"))
        self.assertIsNone(series_key("gcal:evt-9:tick:"))
        self.assertIsNone(series_key("gcal:evt-9:tick:2026-06-01"))

    def test_none_is_not_a_series(self):
        self.assertIsNone(series_key(None))
        self.assertIsNone(series_key(""))


# ── Row building ────────────────────────────────────────────────────────────


class TestBuildTriageRows(unittest.TestCase):
    def test_series_collapses_to_one_row_covering_every_tick(self):
        moments = _tick_moments()
        events = [
            _rec(
                i + 1,
                ts=moment,
                external_id=_tick_external_id("gcal:m", moment),
                subject="Standup",
            )
            for i, moment in enumerate(moments)
        ]
        rows = build_triage_rows(events)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].display_key, "gcal:m:tick:")
        self.assertEqual(rows[0].count, 13)
        self.assertEqual(rows[0].event_ids, tuple(range(1, 14)))

    def test_lone_events_stay_separate(self):
        events = [
            _rec(1, external_id="gcal:solo", subject="1:1"),
            _rec(2, external_id=None, subject="hook"),
        ]
        rows = build_triage_rows(events)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].display_key, "gcal:solo")
        self.assertEqual(rows[1].display_key, "event#2")

    def test_series_and_lone_together(self):
        first, second = _tick_moments()[:2]
        events = [_rec(1, ts=first, external_id=_tick_external_id("gcal:m", first))]
        events += [_rec(2, ts=second, external_id=_tick_external_id("gcal:m", second))]
        events += [_rec(3, external_id=None, subject="lone")]
        rows = build_triage_rows(events)
        self.assertEqual([r.count for r in rows], [2, 1])

    def test_representative_is_earliest_member(self):
        base = MEETING_START
        events = [
            _rec(
                1,
                external_id=_tick_external_id("gcal:m", base),
                ts=base,
                subject="first",
            ),
            _rec(
                2,
                external_id=_tick_external_id("gcal:m", base + timedelta(minutes=5)),
                ts=base + timedelta(minutes=5),
            ),
        ]
        rows = build_triage_rows(events)
        self.assertEqual(rows[0].subject, "first")
        self.assertEqual(rows[0].timestamp, base.isoformat())


# ── Line composition ────────────────────────────────────────────────────────


class TestTriageBodyLines(unittest.TestCase):
    def _rows(self):
        return [
            TriageRow("gcal:m:tick:", (1, 2, 3), "chatter", "2026-06-01T09:00:00", "Standup"),
            TriageRow("gcal:solo", (4,), "chatter", "2026-06-01T10:00:00", "1:1"),
        ]

    def test_one_line_per_row(self):
        lines = triage_body_lines(self._rows(), 0)
        self.assertEqual(len(lines), 2)

    def test_selected_row_marked(self):
        body = "\n".join(triage_body_lines(self._rows(), 1))
        self.assertIn("> chatter", body)  # the marker sits on the selected row
        self.assertIn("Standup", body)
        # Only the selected row carries the marker.
        self.assertEqual(body.count(">"), 1)

    def test_series_count_shown(self):
        body = "\n".join(triage_body_lines(self._rows(), 0))
        self.assertIn("x3", body)

    def test_lone_row_shows_no_count(self):
        line = row_line(self._rows()[1], False)
        self.assertNotIn("x1", line)
        self.assertIn("1:1", line)

    def test_display_key_stands_in_for_missing_subject(self):
        row = TriageRow("gcal:solo", (4,), "chatter", "2026-06-01T10:00:00", "")
        self.assertIn("gcal:solo", row_line(row, False))

    def test_empty_queue_message(self):
        lines = triage_body_lines([], 0)
        self.assertIn("nothing to triage", "\n".join(lines))


# ── Transitions (pure, over a fixture DB) ───────────────────────────────────


def _fixture_store():
    """A DB with a 13-tick series and one lone unattributed event on 2026-06-01.

    The ticks are keyed exactly as the calendar resync writes them, so this
    fixture exercises the real ingestion→triage key contract.
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    store = make_state_db(Path(tmp.name))
    base = MEETING_START
    for moment in _tick_moments():
        store.add_event(
            EventRecord(
                id=None,
                source="chatter",
                timestamp=moment,
                task_ids=[],
                repo="",
                subject="Standup",
                external_id=_tick_external_id("gcal:evt-9", moment),
            )
        )
    store.add_event(
        EventRecord(
            id=None,
            source="chatter",
            timestamp=base + timedelta(hours=3),
            task_ids=[],
            repo="",
            subject="1:1 with Sam",
            external_id="gcal:solo-1",
        )
    )
    return store


class TestTriageTransitionsOverFixture(unittest.TestCase):
    def _state(self):
        return AppState(window=DateWindow(date(2026, 6, 1), date(2026, 6, 1)), sessions=[])

    def test_enter_triage_lists_series_and_lone_rows(self):
        deps = _deps(_fixture_store())
        opened = enter_triage(deps, self._state())
        self.assertEqual(opened.mode, "triage")
        # One row for the whole 13-tick series + one for the lone event.
        self.assertEqual(len(opened.triage_rows), 2)
        series = opened.triage_rows[0]
        self.assertEqual(series.count, 13)
        self.assertEqual(series.display_key, "gcal:evt-9:tick:")

    def test_digits_build_input_and_backspace_edits(self):
        deps = _deps(_fixture_store())
        state = enter_triage(deps, self._state())
        for ch in "2464":
            state = type_triage_digit(state, ch)
        self.assertEqual(state.triage_input, "2464")
        state = erase_triage_digit(state)
        self.assertEqual(state.triage_input, "246")

    def test_down_moves_selection_and_clears_input(self):
        deps = _deps(_fixture_store())
        state = enter_triage(deps, self._state())
        state = type_triage_digit(state, "9")
        state = move_triage_selection(state, 1)
        self.assertEqual(state.triage_selected, 1)
        self.assertEqual(state.triage_input, "")

    def test_up_clamps_at_top(self):
        deps = _deps(_fixture_store())
        state = enter_triage(deps, self._state())
        state = move_triage_selection(state, -1)
        self.assertEqual(state.triage_selected, 0)

    def test_exit_returns_to_main(self):
        deps = _deps(_fixture_store())
        state = enter_triage(deps, self._state())
        state = exit_triage(state)
        self.assertEqual(state.mode, "main")

    def test_move_on_empty_queue_is_noop(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        empty = make_state_db(Path(tmp.name))
        deps = _deps(empty)
        state = enter_triage(deps, self._state())
        result = move_triage_selection(state, 1)
        self.assertEqual(result, state)

    def test_invalid_task_id_reports_and_does_not_assign(self):
        store = _fixture_store()
        deps = _deps(store)
        state = enter_triage(deps, self._state())
        # No digits typed → assign is rejected.
        state = assign_triage(deps, state)
        self.assertIn("invalid task id", state.status)
        self.assertEqual(len(state.triage_rows), 2)  # nothing dropped out

    def test_zero_is_not_a_valid_task_id(self):
        deps = _deps(_fixture_store())
        state = enter_triage(deps, self._state())
        state = type_triage_digit(state, "0")
        state = assign_triage(deps, state)
        self.assertIn("invalid task id", state.status)

    def test_assign_on_empty_queue_is_guarded(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        empty = make_state_db(Path(tmp.name))
        deps = _deps(empty)
        state = enter_triage(deps, self._state())
        state = replace_input(state, "24648")
        state = assign_triage(deps, state)
        self.assertIn("nothing to triage", state.status)


def replace_input(state, text):
    from dataclasses import replace

    return replace(state, triage_input=text)


class TestTriageEndToEnd(unittest.TestCase):
    """The full recipe: open → assign the 13-tick series → it derives a session."""

    def test_series_assignment_updates_all_ticks_and_derives_a_session(self):
        store = _fixture_store()
        deps = _deps(store)
        window = DateWindow(date(2026, 6, 1), date(2026, 6, 1))
        state = AppState(window=window, sessions=[])

        # Open triage: one row for the series, one for the lone event.
        state = enter_triage(deps, state)
        self.assertEqual(len(state.triage_rows), 2)
        series_ids = list(state.triage_rows[0].event_ids)
        self.assertEqual(len(series_ids), 13)

        # Type the task id and assign the whole series in one transaction.
        for ch in "24648":
            state = type_triage_digit(state, ch)
        state = assign_triage(deps, state)

        self.assertIn("assigned 13 events of series gcal:evt-9:tick: to task 24648", state.status)
        # All 13 ticks now carry the task id.
        for event_id in series_ids:
            self.assertEqual(store.get_event(event_id).task_ids, ["24648"])
        # The assigned series dropped out; only the lone event remains to triage.
        self.assertEqual(len(state.triage_rows), 1)
        self.assertEqual(state.triage_rows[0].display_key, "gcal:solo-1")

        # The events are now derivable — the meeting bills instead of vanishing.
        lo = datetime(2026, 6, 1, tzinfo=UTC)
        hi = datetime(2026, 6, 2, tzinfo=UTC)
        sessions = store.derive_sessions_overlapping(lo, hi, gap_secs=3600)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].task_id, "24648")
        self.assertEqual(sessions[0].event_ids, tuple(series_ids))
        # The whole meeting bills: one assign covered all 13 ticks, so the window
        # spans the full hour instead of collapsing to a zero-length (min-billed)
        # session — the under-billing #370 exists to prevent.
        self.assertEqual(sessions[0].duration_seconds, 3600.0)


if __name__ == "__main__":
    unittest.main()
