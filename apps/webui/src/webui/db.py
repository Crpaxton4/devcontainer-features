"""Read-only SQLite access. Every page reads through ``rows``."""

import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any


def rows(path: Path, sql: str, args: Sequence[Any] = ()) -> list[dict[str, Any]]:
    """Run one query on ``path`` opened with ``mode=ro``; return a list of dicts.

    A missing file or a missing table gives ``[]``: a fresh host has no data yet.
    Any other database error is raised.
    """
    if not Path(path).is_file():
        return []
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in conn.execute(sql, tuple(args))]
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return []
        raise
    finally:
        conn.close()
