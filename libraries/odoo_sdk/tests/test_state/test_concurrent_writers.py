"""Concurrency tests for the SQLite WAL + busy_timeout pragmas (issue #357).

Before ``_connect`` enabled WAL and a busy timeout, two simultaneous
``add_event`` writers against one DB file raced on the default rollback journal
with a 0ms lock timeout: the loser got an immediate ``database is locked`` and
its event was silently dropped by the swallowing callers (hook ``|| true``, MCP
``try/except pass``). These tests prove both writers now persist every event and
that the DB file is actually in WAL mode.
"""

import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path

from odoo_sdk.state import EventRecord, LocalStateClient

UTC = timezone.utc

WRITERS = 2
EVENTS_PER_WRITER = 50


def _tmp_path() -> Path:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Path(tmp.name)


def _event(writer: int, seq: int) -> EventRecord:
    return EventRecord(
        id=None,
        source="commit",
        timestamp=datetime(2026, 6, 1, 9, 0, tzinfo=UTC),
        task_ids=[str(writer)],
        repo="owner/repo",
        subject=f"writer {writer} event {seq}",
    )


class TestConcurrentWriters(unittest.TestCase):
    def test_two_writers_no_silent_drops(self) -> None:
        """Two threads, each its own client, hammer ``add_event`` on one DB.

        Every write must persist: the final row count equals the total number
        of ``add_event`` calls, proving the busy timeout waited out the lock
        instead of dropping the loser's event.
        """
        db_path = _tmp_path()
        # Materialize the schema up front so both writers start from a ready DB.
        LocalStateClient(db_path=db_path)

        barrier = threading.Barrier(WRITERS)
        errors: list[Exception] = []

        def hammer(writer: int) -> None:
            client = LocalStateClient(db_path=db_path)
            barrier.wait()  # maximize contention: release all writers together
            try:
                for seq in range(EVENTS_PER_WRITER):
                    client.add_event(_event(writer, seq))
            except Exception as exc:  # pragma: no cover - failure path
                errors.append(exc)

        threads = [
            threading.Thread(target=hammer, args=(w,)) for w in range(WRITERS)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        conn = sqlite3.connect(str(db_path))
        try:
            count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(count, WRITERS * EVENTS_PER_WRITER)

    def test_connect_uses_wal_journal_mode(self) -> None:
        """``_connect`` must leave the DB file in WAL journal mode."""
        client = LocalStateClient(db_path=_tmp_path())
        conn = client._connect()
        try:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(mode.lower(), "wal")

    def test_connect_sets_busy_timeout(self) -> None:
        """``_connect`` must set a non-zero busy timeout on every connection."""
        client = LocalStateClient(db_path=_tmp_path())
        conn = client._connect()
        try:
            timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(timeout, 2000)


if __name__ == "__main__":
    unittest.main()
