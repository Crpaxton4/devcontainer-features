"""Numbered, forward-only migration runner and store connection (section 11).

Rules: each migration is one transaction; each is recorded in the database in
``schema_migrations``; none is conditional on inspecting the current shape.
The only thing consulted is the ledger of recorded version numbers, which is
history and not shape. A migration that fails rolls back and is not recorded.

Connections are opened by :func:`open_store` in autocommit mode
(``isolation_level=None``) so that every transaction is an explicit
``BEGIN IMMEDIATE`` through :func:`transaction`. The settings follow section 11:
``journal_mode=WAL`` (asserted), ``busy_timeout=5000``, ``foreign_keys=ON`` and
a per-store ``synchronous``.
"""

import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

#: Per-connection ``busy_timeout`` in milliseconds (section 11).
BUSY_TIMEOUT_MS = 5000

_LEDGER_DDL = (
    "CREATE TABLE IF NOT EXISTS schema_migrations ("
    "version INTEGER PRIMARY KEY CHECK (version >= 1), "
    "name TEXT NOT NULL, "
    "applied_us INTEGER NOT NULL) STRICT"
)


class MigrationError(RuntimeError):
    """The migration list is malformed (not numbered 1..N without gaps)."""


@dataclass(frozen=True)
class Migration:
    """One forward-only migration: a version, a name and its statements.

    ``statements`` are single SQL statements run in order inside one
    transaction. They are separate strings (not a script) because a script
    runner would commit implicitly, and a trigger body contains semicolons.
    """

    version: int
    name: str
    statements: tuple[str, ...]


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run the block in ``BEGIN IMMEDIATE`` ... ``COMMIT``; roll back on error."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def _validate(migrations: Sequence[Migration]) -> None:
    for expected, migration in enumerate(migrations, start=1):
        if migration.version != expected:
            raise MigrationError(
                f"migration {migration.name!r} has version {migration.version}, "
                f"expected {expected}: versions are 1..N in order, no gaps"
            )


def _apply_one(conn: sqlite3.Connection, migration: Migration, now_us: int) -> None:
    with transaction(conn):
        recorded = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version = ?", (migration.version,)
        ).fetchone()
        if recorded:  # another process won the race while we waited for the lock
            return
        for statement in migration.statements:
            conn.execute(statement)
        conn.execute(
            "INSERT INTO schema_migrations (version, name, applied_us) "
            "VALUES (?, ?, ?)",
            (migration.version, migration.name, now_us),
        )


def apply_migrations(
    conn: sqlite3.Connection, migrations: Sequence[Migration], now_us: int
) -> list[int]:
    """Apply every unrecorded migration in order; return the versions applied.

    ``conn`` must be in autocommit mode (see :func:`open_store`).
    """
    _validate(migrations)
    conn.execute(_LEDGER_DDL)
    done = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
    applied = []
    for migration in migrations:
        if migration.version not in done:
            _apply_one(conn, migration, now_us)
            applied.append(migration.version)
    return applied


def open_store(
    path: Path,
    migrations: Sequence[Migration],
    *,
    synchronous: str,
    now_us: int,
) -> sqlite3.Connection:
    """Open (creating on first use) the database at ``path`` and migrate it.

    ``synchronous`` is ``"FULL"`` or ``"NORMAL"``. The returned connection has
    ``row_factory = sqlite3.Row`` and may be shared across threads only behind
    the caller's own lock (the stores do this).
    """
    if synchronous not in ("FULL", "NORMAL"):
        raise ValueError(f"synchronous must be FULL or NORMAL, not {synchronous!r}")
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
    if mode != "wal":
        conn.close()
        raise sqlite3.OperationalError(f"{path}: journal_mode is {mode!r}, not 'wal'")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(f"PRAGMA synchronous={synchronous}")
    apply_migrations(conn, migrations, now_us)
    return conn
