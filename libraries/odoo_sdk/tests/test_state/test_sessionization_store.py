import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from odoo_sdk.state import EventRecord, LocalStateClient

UTC = timezone.utc


def _tmp_db() -> LocalStateClient:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return LocalStateClient(db_path=Path(tmp.name))


def _event(minute: int, task="101", source="commit") -> EventRecord:
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
    )


class TestEventStore(unittest.TestCase):
    def test_add_and_get_event_roundtrips(self):
        db = _tmp_db()
        stored = db.add_event(_event(0))
        self.assertIsNotNone(stored.id)
        fetched = db.get_event(stored.id)
        self.assertEqual(fetched.task_ids, ["101"])
        self.assertEqual(fetched.payload, {"pr_title": "T", "pr_body": "B"})

    def test_get_missing_event_returns_none(self):
        self.assertIsNone(_tmp_db().get_event(999))

    def test_get_events_ordered_and_range_bounded(self):
        db = _tmp_db()
        db.add_event(_event(30))
        db.add_event(_event(0))
        events = db.get_events()
        self.assertEqual([e.timestamp.minute for e in events], [0, 30])
        bounded = db.get_events(
            start=datetime(2026, 6, 1, 9, 10, tzinfo=UTC),
            end=datetime(2026, 6, 1, 9, 45, tzinfo=UTC),
        )
        self.assertEqual(len(bounded), 1)

    def test_null_payload_roundtrips(self):
        db = _tmp_db()
        rec = _event(0)
        rec.payload = None
        stored = db.add_event(rec)
        self.assertIsNone(db.get_event(stored.id).payload)

    def test_coexists_with_fsm_store(self):
        db = _tmp_db()
        db.add_event(_event(0))
        run = db.create_run(1, "Task 1", 10, "Proj", timesheet_id=5)
        self.assertIsNotNone(run.id)
        self.assertEqual(len(db.get_events()), 1)


# The OLD schema, as it existed before #330 removed the materialized sessions
# read path: a ``sessions`` table plus an ``events`` table whose ``session_id``
# links each event to a session (declared ON DELETE SET NULL).
_LEGACY_SCHEMA = """
CREATE TABLE events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    source     TEXT    NOT NULL,
    timestamp  TEXT    NOT NULL,
    task_ids   TEXT    NOT NULL DEFAULT '[]',
    repo       TEXT    NOT NULL DEFAULT '',
    pr_num     INTEGER NOT NULL DEFAULT 0,
    branch     TEXT    NOT NULL DEFAULT '',
    subject    TEXT    NOT NULL DEFAULT '',
    payload    TEXT,
    session_id INTEGER REFERENCES sessions (id) ON DELETE SET NULL
);
CREATE TABLE sessions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id       TEXT    NOT NULL,
    repo          TEXT    NOT NULL DEFAULT '',
    started_at    TEXT    NOT NULL,
    ended_at      TEXT    NOT NULL,
    strategy_name TEXT    NOT NULL DEFAULT 'development',
    category      TEXT    NOT NULL DEFAULT 'Development',
    pr_num        INTEGER NOT NULL DEFAULT 0
);
"""


class TestDropMaterializedSessionsMigration(unittest.TestCase):
    def _legacy_db_path(self) -> Path:
        """Build a pre-#330 DB by hand: a sessions row + linked events."""
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        conn = sqlite3.connect(tmp.name)
        conn.executescript(_LEGACY_SCHEMA)
        conn.execute(
            "INSERT INTO sessions (id, task_id, repo, started_at, ended_at) "
            "VALUES (1, '101', 'owner/repo', "
            "'2026-06-01T09:00:00+00:00', '2026-06-01T09:40:00+00:00')"
        )
        # Two events 20m apart, both linked to session 1.
        for minute in (0, 20):
            conn.execute(
                "INSERT INTO events (source, timestamp, task_ids, repo, pr_num, "
                "branch, subject, payload, session_id) VALUES "
                "('commit', ?, '[\"101\"]', 'owner/repo', 0, 'main', 's', NULL, 1)",
                (f"2026-06-01T09:{minute:02d}:00+00:00",),
            )
        conn.commit()
        conn.close()
        return Path(tmp.name)

    def test_open_drops_sessions_table_and_keeps_events(self):
        path = self._legacy_db_path()
        db = LocalStateClient(db_path=path)

        # The materialized sessions table is gone.
        with sqlite3.connect(str(path)) as raw:
            tables = {
                row[0]
                for row in raw.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        self.assertNotIn("sessions", tables)

        # Events survive the migration and are queryable.
        events = db.get_events()
        self.assertEqual(len(events), 2)
        self.assertEqual([e.timestamp.minute for e in events], [0, 20])
        self.assertEqual(events[0].task_ids, ["101"])

    def test_derive_still_works_over_migrated_events(self):
        path = self._legacy_db_path()
        db = LocalStateClient(db_path=path)
        # The two events are 20m apart; a 60m gap keeps them one whole session.
        windows = db.derive_sessions_overlapping(
            datetime(2026, 6, 1, 0, tzinfo=UTC),
            datetime(2026, 6, 2, 0, tzinfo=UTC),
            gap_secs=3600,
        )
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0].task_id, "101")
        self.assertEqual(windows[0].started_at.minute, 0)
        self.assertEqual(windows[0].ended_at.minute, 20)

    def test_can_write_events_after_migration(self):
        # The legacy ``events`` table carries an orphaned
        # ``session_id REFERENCES sessions`` FK. Once the parent table is
        # dropped, inserts must still succeed (FK enforcement is off), or the
        # tracker could never append another event to a migrated DB.
        path = self._legacy_db_path()
        db = LocalStateClient(db_path=path)
        stored = db.add_event(_event(45))
        self.assertIsNotNone(stored.id)
        self.assertEqual(len(db.get_events()), 3)

    def test_migration_is_idempotent(self):
        path = self._legacy_db_path()
        LocalStateClient(db_path=path)
        # A second open (sessions already dropped) must not fail.
        LocalStateClient(db_path=path)


if __name__ == "__main__":
    unittest.main()
