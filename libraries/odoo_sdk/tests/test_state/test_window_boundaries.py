"""Window-boundary timestamp comparison tests (issue #360).

Stored event timestamps are normalized to the uniform ``...+00:00`` form on
insert (:func:`odoo_sdk.state.db._normalize_utc_isoformat`), and the read path
compares query bounds against them *as strings*. A query bound must therefore
be shaped identically — including naive bounds such as the TUI's
``datetime.combine(date, time.min)`` window edges, which reach the store without
a timezone. These tests pin the invariant that an event stamped exactly on a
window edge is included/excluded identically by ``count_events``, ``get_events``,
and the derivation, whether the bound arrives naive or aware.
"""

import tempfile
import unittest
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from odoo_sdk.state import EventRecord, LocalStateClient
from odoo_sdk.state.db import _bound_isoformat, _normalize_utc_isoformat
from tests.support import make_state_db

UTC = timezone.utc
GAP = 3600  # one hour


def _tmp_state() -> LocalStateClient:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return make_state_db(Path(tmp.name))


def _event(state, *, ts, task_ids=("101",), repo=""):
    return state.add_event(
        EventRecord(
            id=None,
            source="agent",
            timestamp=ts,
            task_ids=list(task_ids),
            repo=repo,
            pr_num=0,
        )
    )


class TestBoundFormatting(unittest.TestCase):
    """The bound helper must emit the exact stored-timestamp string shape."""

    def test_naive_bound_gets_utc_suffix(self):
        # The TUI builds naive midnight bounds; they must be stamped +00:00 so
        # they compare byte-for-byte against stored rows.
        naive = datetime(2026, 6, 1, 0, 0, 0)
        self.assertEqual(_bound_isoformat(naive), "2026-06-01T00:00:00+00:00")

    def test_naive_bound_matches_stored_normalization(self):
        # A naive bound and the stored form of the same wall-clock instant must
        # be identical strings.
        instant = datetime(2026, 6, 1, 0, 0, 0)
        self.assertEqual(
            _bound_isoformat(instant),
            _normalize_utc_isoformat(instant),
        )

    def test_aware_non_utc_bound_converted_to_utc(self):
        aware = datetime(2026, 6, 1, 8, 0, 0, tzinfo=timezone(timedelta(hours=-4)))
        self.assertEqual(_bound_isoformat(aware), "2026-06-01T12:00:00+00:00")

    def test_open_range_sentinels_still_sort_past_every_row(self):
        # datetime.min/max are naive sentinels from the query layer; suffixing
        # them must not disturb their role as outer bounds.
        self.assertLess(_bound_isoformat(datetime.min), "2026-06-01T00:00:00+00:00")
        self.assertGreater(_bound_isoformat(datetime.max), "9998-01-01T00:00:00+00:00")


class TestLowerBoundEdge(unittest.TestCase):
    """An event exactly at the inclusive lower edge is included everywhere."""

    def test_event_on_start_edge_included_consistently(self):
        state = _tmp_state()
        edge = datetime(2026, 6, 1, 0, 0, 0, tzinfo=UTC)
        _event(state, ts=edge)
        # TUI-style naive window bounds straddling the edge.
        lo = datetime.combine(edge.date(), time.min)  # naive midnight == edge
        hi = datetime.combine(edge.date() + timedelta(days=1), time.min)

        self.assertEqual(state.count_events(lo, hi), 1)
        self.assertEqual(len(state.get_events(lo, hi)), 1)
        windows = state.derive_sessions_overlapping(lo, hi, gap_secs=GAP)
        self.assertEqual(len(windows), 1)
        self.assertEqual(len(windows[0].event_ids), 1)

    def test_naive_and_aware_start_bounds_agree(self):
        state = _tmp_state()
        edge = datetime(2026, 6, 1, 0, 0, 0, tzinfo=UTC)
        _event(state, ts=edge)
        _event(state, ts=edge + timedelta(hours=2))
        hi = datetime(2026, 6, 2, 0, 0, 0, tzinfo=UTC)

        naive_lo = datetime(2026, 6, 1, 0, 0, 0)
        aware_lo = datetime(2026, 6, 1, 0, 0, 0, tzinfo=UTC)
        self.assertEqual(
            state.count_events(naive_lo, hi),
            state.count_events(aware_lo, hi),
        )
        self.assertEqual(
            [e.id for e in state.get_events(naive_lo, hi)],
            [e.id for e in state.get_events(aware_lo, hi)],
        )


class TestUpperBoundEdge(unittest.TestCase):
    """count_events/get_events share half-open [start, end); they must agree."""

    def test_event_on_end_edge_excluded_by_both(self):
        state = _tmp_state()
        edge = datetime(2026, 6, 2, 0, 0, 0, tzinfo=UTC)
        _event(state, ts=edge)  # exactly at the exclusive upper bound
        lo = datetime(2026, 6, 1, 0, 0, 0)  # naive
        hi = datetime(2026, 6, 2, 0, 0, 0)  # naive == edge

        # Half-open upper bound: the on-edge event is excluded by both readers,
        # identically.
        self.assertEqual(state.count_events(lo, hi), 0)
        self.assertEqual(state.get_events(lo, hi), [])

    def test_count_and_get_events_agree_for_aware_non_utc_bound(self):
        # Regression: get_events formerly used raw ``.isoformat()`` for bounds
        # while count_events normalized to UTC, so an aware non-UTC bound made
        # them disagree by string comparison. An event one second before the
        # bound instant must be excluded by BOTH.
        state = _tmp_state()
        _event(state, ts=datetime(2026, 6, 1, 11, 59, 59, tzinfo=UTC))
        # 08:00-04:00 is the same instant as 12:00+00:00.
        lo = datetime(2026, 6, 1, 8, 0, 0, tzinfo=timezone(timedelta(hours=-4)))
        hi = datetime(2026, 6, 2, 0, 0, 0, tzinfo=UTC)

        self.assertEqual(state.count_events(lo, hi), 0)
        self.assertEqual(state.get_events(lo, hi), [])
        self.assertEqual(
            state.count_events(lo, hi),
            len(state.get_events(lo, hi)),
        )


class TestDerivationEndEdge(unittest.TestCase):
    """The derivation window is inclusive on ``end`` (overlap semantics)."""

    def test_session_starting_on_end_edge_included(self):
        # Fix target: with a naive end bound, a session starting exactly on the
        # edge was excluded because "..+00:00" sorts after "..00:00" in the
        # HAVING string compare. Normalizing the bound includes it as intended.
        state = _tmp_state()
        edge = datetime(2026, 6, 2, 0, 0, 0, tzinfo=UTC)
        _event(state, ts=edge)
        lo = datetime(2026, 6, 1, 0, 0, 0)  # naive
        hi = datetime(2026, 6, 2, 0, 0, 0)  # naive == edge (inclusive)

        windows = state.derive_sessions_overlapping(lo, hi, gap_secs=GAP)
        self.assertEqual(len(windows), 1)
        self.assertEqual(len(windows[0].event_ids), 1)

    def test_naive_and_aware_end_bounds_agree(self):
        state = _tmp_state()
        edge = datetime(2026, 6, 2, 0, 0, 0, tzinfo=UTC)
        _event(state, ts=edge)
        lo = datetime(2026, 6, 1, 0, 0, 0, tzinfo=UTC)

        naive_hi = datetime(2026, 6, 2, 0, 0, 0)
        aware_hi = datetime(2026, 6, 2, 0, 0, 0, tzinfo=UTC)
        naive_windows = state.derive_sessions_overlapping(lo, naive_hi, gap_secs=GAP)
        aware_windows = state.derive_sessions_overlapping(lo, aware_hi, gap_secs=GAP)
        self.assertEqual(
            [w.event_ids for w in naive_windows],
            [w.event_ids for w in aware_windows],
        )


if __name__ == "__main__":
    unittest.main()
