"""Acceptance tests for the single central tracker DB (issue #369).

These exercise the two derivation guarantees the central DB exists to provide:

1. Events logged from two *different* repositories for the SAME task in one time
   window land in one DB and derive as exactly ONE session (one timesheet line) —
   the whole point of unifying the previously per-repo databases.
2. Events for DIFFERENT tasks in the same window still derive as separate,
   concurrent sessions and bill in parallel (parallel billing is intended and
   must survive the change).
"""

import unittest
from datetime import datetime, timezone

from odoo_sdk.state import EventRecord
from tests.support import make_state_db

UTC = timezone.utc
GAP = 3600  # one hour


def _event(db, *, task, repo, minute):
    db.add_event(
        EventRecord(
            id=None,
            source="commit",
            timestamp=datetime(2026, 6, 1, 9, minute, tzinfo=UTC),
            task_ids=[task],
            repo=repo,
            subject="work",
        )
    )


class TestCentralDbDerivation(unittest.TestCase):
    def setUp(self):
        self.db = make_state_db()
        self.start = datetime(2026, 6, 1, 0, tzinfo=UTC)
        self.end = datetime(2026, 6, 2, 0, tzinfo=UTC)

    def _derive(self, **kw):
        return self.db.derive_sessions_overlapping(
            self.start, self.end, gap_secs=GAP, **kw
        )

    def test_two_repos_same_task_one_window_derive_one_session(self):
        # Same task worked from two normalized repo labels within one gap window.
        _event(self.db, task="24648", repo="owner/repo-a", minute=0)
        _event(self.db, task="24648", repo="owner/repo-b", minute=20)
        windows = self._derive()
        self.assertEqual(len(windows), 1)
        window = windows[0]
        self.assertEqual(window.task_id, "24648")
        # Both events compose the single session.
        self.assertEqual(len(window.event_ids), 2)
        # The window spans both repos' events (00:00 → 00:20).
        self.assertEqual(window.started_at.minute, 0)
        self.assertEqual(window.ended_at.minute, 20)

    def test_agentless_and_repo_events_same_task_merge(self):
        # An agent event (repo="") and a resync commit event (repo set) for one
        # task must still be ONE session, not two parallel lanes.
        _event(self.db, task="24648", repo="", minute=0)
        _event(self.db, task="24648", repo="owner/repo-a", minute=10)
        windows = self._derive()
        self.assertEqual(len(windows), 1)
        self.assertEqual(len(windows[0].event_ids), 2)

    def test_different_tasks_derive_separate_parallel_sessions(self):
        # Two distinct tasks in the same window remain separate lanes.
        _event(self.db, task="100", repo="owner/repo-a", minute=0)
        _event(self.db, task="200", repo="owner/repo-b", minute=5)
        windows = self._derive()
        self.assertEqual({w.task_id for w in windows}, {"100", "200"})
        self.assertEqual(len(windows), 2)


if __name__ == "__main__":
    unittest.main()
