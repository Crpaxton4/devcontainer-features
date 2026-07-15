"""Tests for the triage DB primitives (issue #370, acceptance item 9).

:meth:`LocalStateClient.get_unattributed_events` reads events ingested with an
empty ``task_ids`` array (invisible to billing) and
:meth:`LocalStateClient.assign_event_task_ids` attributes a set of them to a task
in one transaction. Together they back the TUI triage surface.
"""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from odoo_sdk.state import EventRecord, LocalStateClient
from tests.support import make_state_db

UTC = timezone.utc


def _tmp_state() -> LocalStateClient:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return make_state_db(Path(tmp.name))


def _event(state, *, ts, task_ids=(), source="chatter", external_id=None, subject=""):
    return state.add_event(
        EventRecord(
            id=None,
            source=source,
            timestamp=ts,
            task_ids=list(task_ids),
            repo="",
            subject=subject,
            external_id=external_id,
        )
    )


class TestGetUnattributedEvents(unittest.TestCase):
    def test_returns_only_events_with_empty_task_ids(self):
        state = _tmp_state()
        base = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
        lone = _event(state, ts=base)
        _event(state, ts=base + timedelta(minutes=1), task_ids=("101",))  # attributed
        rows = state.get_unattributed_events()
        self.assertEqual([r.id for r in rows], [lone.id])

    def test_orders_by_timestamp(self):
        state = _tmp_state()
        base = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
        later = _event(state, ts=base + timedelta(hours=2))
        earlier = _event(state, ts=base)
        rows = state.get_unattributed_events()
        self.assertEqual([r.id for r in rows], [earlier.id, later.id])

    def test_window_is_half_open(self):
        state = _tmp_state()
        start = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
        end = datetime(2026, 6, 2, 0, 0, tzinfo=UTC)
        inside = _event(state, ts=datetime(2026, 6, 1, 12, 0, tzinfo=UTC))
        _event(state, ts=start - timedelta(seconds=1))  # before window
        _event(state, ts=end)  # exactly the exclusive upper edge
        rows = state.get_unattributed_events(start, end)
        self.assertEqual([r.id for r in rows], [inside.id])

    def test_ignores_source_predicate(self):
        # Triage must see every unattributed source, even ones that never
        # sessionize (merge/review), so nothing silently escapes billing.
        state = _tmp_state()
        base = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
        review = _event(state, ts=base, source="review")
        rows = state.get_unattributed_events()
        self.assertEqual([r.id for r in rows], [review.id])


class TestAssignEventTaskIds(unittest.TestCase):
    def test_attributes_every_id_in_one_call(self):
        state = _tmp_state()
        base = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
        ids = [_event(state, ts=base + timedelta(minutes=i)).id for i in range(3)]
        updated = state.assign_event_task_ids(ids, 24648)
        self.assertEqual(updated, 3)
        for event_id in ids:
            self.assertEqual(state.get_event(event_id).task_ids, ["24648"])

    def test_empty_id_list_is_noop(self):
        state = _tmp_state()
        self.assertEqual(state.assign_event_task_ids([], 24648), 0)

    def test_rejects_non_positive_task_id(self):
        state = _tmp_state()
        for bad in (0, -5):
            with self.assertRaises(ValueError):
                state.assign_event_task_ids([1], bad)

    def test_rejects_boolean_task_id(self):
        # ``True`` is an int subclass; a boolean is not a valid task id.
        state = _tmp_state()
        with self.assertRaises(ValueError):
            state.assign_event_task_ids([1], True)

    def test_reattributed_events_become_derivable(self):
        # The whole point: an unattributed event does not derive; once assigned it
        # does. Sources are chosen to participate in sessionization.
        state = _tmp_state()
        base = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
        ids = [_event(state, ts=base + timedelta(minutes=5 * i)).id for i in range(3)]
        lo = datetime(2026, 6, 1, tzinfo=UTC)
        hi = datetime(2026, 6, 2, tzinfo=UTC)
        self.assertEqual(state.derive_sessions_overlapping(lo, hi, gap_secs=3600), [])
        state.assign_event_task_ids(ids, 24648)
        sessions = state.derive_sessions_overlapping(lo, hi, gap_secs=3600)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].task_id, "24648")
        self.assertEqual(sessions[0].event_ids, tuple(ids))


if __name__ == "__main__":
    unittest.main()
