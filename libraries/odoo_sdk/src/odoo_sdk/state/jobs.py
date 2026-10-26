"""The job store: ``jobs.db``, the dispatcher's disposable working state.

Written by two processes: the dispatcher, and ``dispatch submit``, which inserts
PENDING rows without the dispatcher lock. WAL mode and ``busy_timeout=5000``
(see :mod:`odoo_sdk.state.migrations`) cover the second writer. Regenerable, and
safe to delete after a quiet week without losing history (the run record is in
``runs.db``). Callers use :class:`JobStore` and never see SQL.

The duplicate gate is a property of the engine, not of this code: the partial
unique index ``idx_jobs_one_live`` allows at most one PENDING or RUNNING job
per ``task_id``. :meth:`JobStore.submit` therefore just inserts and translates
the integrity error into :class:`DuplicateTaskInFlightError`. It never queries
first. Use :meth:`JobStore.reject_duplicate` to record the resulting
``DUPLICATE_TASK_IN_FLIGHT`` dead letter.

All time is integer microseconds UTC. ``jobs.outcome`` holds the *stored*
run outcome (see :mod:`odoo_sdk.state.runs_store`) of a DONE job.
"""

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .dispatch_state import jobs_db_path, resolve_now_us
from .migrations import Migration, open_store, transaction


class JobState(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DONE = "DONE"
    DEAD_LETTER = "DEAD_LETTER"


class DeadReason(str, Enum):
    """The ten dead-letter causes (section 18). Each value is a full statement."""

    DUPLICATE_TASK_IN_FLIGHT = "DUPLICATE_TASK_IN_FLIGHT"
    CONTAINER_START_FAILED = "CONTAINER_START_FAILED"
    WORKSPACE_MOUNT_INVALID = "WORKSPACE_MOUNT_INVALID"
    SESSION_LAUNCH_FAILED = "SESSION_LAUNCH_FAILED"
    KILLED_BY_DEADLINE = "KILLED_BY_DEADLINE"
    KILLED_BY_SIGNAL = "KILLED_BY_SIGNAL"
    SESSION_EXIT_NONZERO = "SESSION_EXIT_NONZERO"
    STRUCTURED_OUTPUT_MISSING = "STRUCTURED_OUTPUT_MISSING"
    STRUCTURED_OUTPUT_UNREADABLE = "STRUCTURED_OUTPUT_UNREADABLE"
    ORPHANED_BY_RESTART = "ORPHANED_BY_RESTART"


class DuplicateTaskInFlightError(Exception):
    """A live (PENDING or RUNNING) job already exists for the task."""


class InvalidJobTransitionError(Exception):
    """The job does not exist or is not in a state the operation moves from."""


_DEAD_REASON_SQL = ",\n".join(f"'{reason.value}'" for reason in DeadReason)

JOBS_MIGRATIONS = (
    Migration(
        1,
        "create jobs",
        (
            f"""
CREATE TABLE jobs (
    job_id        TEXT    PRIMARY KEY,
    batch_id      TEXT    NOT NULL,
    task_id       TEXT    NOT NULL CHECK (task_id <> ''),
    repo          TEXT    NOT NULL CHECK (repo <> ''),
    round         INTEGER NOT NULL DEFAULT 1 CHECK (round >= 1),
    state         TEXT    NOT NULL CHECK (state IN
        ('PENDING','RUNNING','DONE','DEAD_LETTER')),
    brief         TEXT    NOT NULL CHECK (brief <> ''),
    submitted_us  INTEGER NOT NULL CHECK (submitted_us > 0),
    started_us    INTEGER CHECK (started_us IS NULL OR started_us >= submitted_us),
    ended_us      INTEGER CHECK (ended_us   IS NULL OR ended_us   >= started_us),
    session_id    TEXT    NOT NULL DEFAULT '',
    container     TEXT    NOT NULL DEFAULT '',
    worktree      TEXT    NOT NULL DEFAULT '',
    outcome       TEXT    NOT NULL DEFAULT '',
    dead_reason   TEXT    NOT NULL DEFAULT '' CHECK (dead_reason IN (
        '',
        {_DEAD_REASON_SQL})),
    detail        TEXT    NOT NULL DEFAULT '',
    submitted_at  TEXT GENERATED ALWAYS AS
        (strftime('%Y-%m-%dT%H:%M:%fZ', submitted_us/1000000.0, 'unixepoch')) VIRTUAL,
    CHECK (state <> 'PENDING' OR started_us IS NULL),
    CHECK (state <> 'RUNNING' OR (started_us IS NOT NULL AND ended_us IS NULL)),
    CHECK (state NOT IN ('DONE','DEAD_LETTER') OR ended_us IS NOT NULL),
    CHECK ((state = 'DEAD_LETTER') = (dead_reason <> ''))
) STRICT""",
            "CREATE UNIQUE INDEX idx_jobs_one_live ON jobs(task_id) "
            "WHERE state IN ('PENDING','RUNNING')",
            "CREATE INDEX idx_jobs_state ON jobs(state, submitted_us)",
        ),
    ),
    Migration(
        2,
        "add jobs.entry",
        ("ALTER TABLE jobs ADD COLUMN entry TEXT NOT NULL DEFAULT ''",),
    ),
)

_COLUMNS = (
    "job_id, batch_id, task_id, repo, round, state, brief, submitted_us, "
    "started_us, ended_us, session_id, container, worktree, outcome, "
    "dead_reason, detail, submitted_at, entry"
)


@dataclass(frozen=True)
class Job:
    """One row of ``jobs``. ``started_us``/``ended_us`` are None when unset."""

    job_id: str
    batch_id: str
    task_id: str
    repo: str
    round: int
    state: JobState
    brief: str
    submitted_us: int
    started_us: int | None
    ended_us: int | None
    session_id: str
    container: str
    worktree: str
    outcome: str
    dead_reason: str
    detail: str
    submitted_at: str
    entry: str = ""

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Job":
        values = dict(zip(row.keys(), tuple(row)))
        values["state"] = JobState(values["state"])
        return cls(**values)


class JobStore:
    """Typed operations over ``jobs.db``. Creates and migrates it on first use."""

    def __init__(self, path: Path | None = None, *, now_us: int | None = None):
        self.path = path or jobs_db_path()
        self._lock = threading.RLock()
        self._conn = open_store(
            self.path,
            JOBS_MIGRATIONS,
            synchronous="NORMAL",
            now_us=resolve_now_us(now_us),
        )

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock, transaction(self._conn) as conn:
            yield conn

    # -- writes ---------------------------------------------------------

    def submit(
        self,
        *,
        job_id: str,
        batch_id: str,
        task_id: str,
        repo: str,
        brief: str,
        round: int = 1,
        entry: str = "",
        now_us: int | None = None,
    ) -> Job:
        """Insert a PENDING job. Raises DuplicateTaskInFlightError on a live twin.

        The unique index decides; nothing is queried first. Any other
        constraint violation propagates as ``sqlite3.IntegrityError``.
        """
        try:
            with self._tx() as conn:
                conn.execute(
                    "INSERT INTO jobs (job_id, batch_id, task_id, repo, round, "
                    "state, brief, submitted_us, entry) "
                    "VALUES (?, ?, ?, ?, ?, 'PENDING', ?, ?, ?)",
                    (
                        job_id,
                        batch_id,
                        task_id,
                        repo,
                        round,
                        brief,
                        resolve_now_us(now_us),
                        entry,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            # SQLITE_CONSTRAINT_UNIQUE is the partial index; a job_id clash is
            # SQLITE_CONSTRAINT_PRIMARYKEY and must propagate.
            if exc.sqlite_errorcode == sqlite3.SQLITE_CONSTRAINT_UNIQUE:
                raise DuplicateTaskInFlightError(task_id) from exc
            raise
        return self.get(job_id)

    def submit_all(self, rows: list[dict], *, now_us: int | None = None) -> list[Job]:
        """Insert every row as PENDING in one transaction, or none of them.

        Each row holds ``job_id, batch_id, task_id, repo, brief`` and an
        optional ``round``. A live twin (in the store or earlier in ``rows``)
        rolls the whole batch back and raises DuplicateTaskInFlightError.
        """
        now = resolve_now_us(now_us)
        task_id = ""
        try:
            with self._tx() as conn:
                for row in rows:
                    task_id = row["task_id"]
                    conn.execute(
                        "INSERT INTO jobs (job_id, batch_id, task_id, repo, round, "
                        "state, brief, submitted_us, entry) "
                        "VALUES (?, ?, ?, ?, ?, 'PENDING', ?, ?, ?)",
                        (
                            row["job_id"],
                            row["batch_id"],
                            task_id,
                            row["repo"],
                            row.get("round", 1),
                            row["brief"],
                            now,
                            row.get("entry", ""),
                        ),
                    )
        except sqlite3.IntegrityError as exc:
            if exc.sqlite_errorcode == sqlite3.SQLITE_CONSTRAINT_UNIQUE:
                raise DuplicateTaskInFlightError(task_id) from exc
            raise
        return self.list_by_batch(rows[0]["batch_id"]) if rows else []

    def reject_duplicate(
        self,
        *,
        job_id: str,
        batch_id: str,
        task_id: str,
        repo: str,
        brief: str,
        round: int = 1,
        detail: str = "",
        entry: str = "",
        now_us: int | None = None,
    ) -> Job:
        """Record the DUPLICATE_TASK_IN_FLIGHT dead letter for a rejected submit.

        The row is inserted directly in DEAD_LETTER, so it never takes part in
        the live-job index. Nothing ran: ``started_us`` stays NULL.
        """
        now = resolve_now_us(now_us)
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO jobs (job_id, batch_id, task_id, repo, round, state, "
                "brief, submitted_us, ended_us, dead_reason, detail, entry) "
                "VALUES (?, ?, ?, ?, ?, 'DEAD_LETTER', ?, ?, ?, ?, ?, ?)",
                (
                    job_id,
                    batch_id,
                    task_id,
                    repo,
                    round,
                    brief,
                    now,
                    now,
                    DeadReason.DUPLICATE_TASK_IN_FLIGHT.value,
                    detail,
                    entry,
                ),
            )
        return self.get(job_id)

    def _transition(self, sql: str, params: tuple, job_id: str, move: str) -> Job:
        with self._tx() as conn:
            changed = conn.execute(sql, params).rowcount
        if changed != 1:
            raise InvalidJobTransitionError(f"{job_id}: cannot {move}")
        return self.get(job_id)

    def mark_running(
        self,
        job_id: str,
        *,
        container: str = "",
        worktree: str = "",
        now_us: int | None = None,
    ) -> Job:
        """PENDING to RUNNING. ``started_us`` is the dispatcher's clock."""
        return self._transition(
            "UPDATE jobs SET state='RUNNING', started_us=?, container=?, worktree=? "
            "WHERE job_id=? AND state='PENDING'",
            (resolve_now_us(now_us), container, worktree, job_id),
            job_id,
            "start a job that is not PENDING",
        )

    def set_session(self, job_id: str, session_id: str) -> Job:
        """Record the session id of a RUNNING job (known after the first event)."""
        return self._transition(
            "UPDATE jobs SET session_id=? WHERE job_id=? AND state='RUNNING'",
            (session_id, job_id),
            job_id,
            "set a session on a job that is not RUNNING",
        )

    def complete(self, job_id: str, *, outcome: str, now_us: int | None = None) -> Job:
        """RUNNING to DONE. ``outcome`` is the stored run outcome value."""
        return self._transition(
            "UPDATE jobs SET state='DONE', ended_us=?, outcome=? "
            "WHERE job_id=? AND state='RUNNING'",
            (resolve_now_us(now_us), outcome, job_id),
            job_id,
            "complete a job that is not RUNNING",
        )

    def dead_letter(
        self,
        job_id: str,
        reason: DeadReason,
        *,
        detail: str = "",
        now_us: int | None = None,
    ) -> Job:
        """PENDING or RUNNING to DEAD_LETTER with a named cause and raw detail."""
        return self._transition(
            "UPDATE jobs SET state='DEAD_LETTER', ended_us=?, dead_reason=?, "
            "detail=? WHERE job_id=? AND state IN ('PENDING','RUNNING')",
            (resolve_now_us(now_us), DeadReason(reason).value, detail, job_id),
            job_id,
            "dead-letter a job that is already finished",
        )

    def reconcile_orphans(
        self, *, detail: str = "", now_us: int | None = None
    ) -> list[str]:
        """Dead-letter every RUNNING job as ORPHANED_BY_RESTART; return their ids.

        Startup calls this before admitting anything: a RUNNING row found at
        startup belongs to a process that died with the previous dispatcher.
        """
        with self._tx() as conn:
            ids = [
                row[0]
                for row in conn.execute(
                    "SELECT job_id FROM jobs WHERE state='RUNNING' ORDER BY submitted_us"
                )
            ]
            conn.execute(
                "UPDATE jobs SET state='DEAD_LETTER', ended_us=?, dead_reason=?, "
                "detail=? WHERE state='RUNNING'",
                (resolve_now_us(now_us), DeadReason.ORPHANED_BY_RESTART.value, detail),
            )
        return ids

    # -- reads ----------------------------------------------------------

    def get(self, job_id: str) -> Job:
        """Return the job, or raise KeyError."""
        with self._lock:
            row = self._conn.execute(
                f"SELECT {_COLUMNS} FROM jobs WHERE job_id=?", (job_id,)
            ).fetchone()
        if row is None:
            raise KeyError(job_id)
        return Job.from_row(row)

    def list_by_state(self, state: JobState) -> list[Job]:
        """Return jobs in ``state``, oldest submission first."""
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {_COLUMNS} FROM jobs WHERE state=? "
                "ORDER BY submitted_us, job_id",
                (JobState(state).value,),
            ).fetchall()
        return [Job.from_row(row) for row in rows]

    def list_by_batch(self, batch_id: str) -> list[Job]:
        """Return the jobs of one batch in insertion order (empty if unknown)."""
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {_COLUMNS} FROM jobs WHERE batch_id=? ORDER BY rowid",
                (batch_id,),
            ).fetchall()
        return [Job.from_row(row) for row in rows]

    def dead_letters(self) -> list[Job]:
        """Return the dead letter list the orchestrator reads."""
        return self.list_by_state(JobState.DEAD_LETTER)

    def count_live(self, repo: str) -> int:
        """Count PENDING plus RUNNING jobs of ``repo`` (the container idle test)."""
        with self._lock:
            return self._conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE repo=? "
                "AND state IN ('PENDING','RUNNING')",
                (repo,),
            ).fetchone()[0]

    def running_task_ids(self) -> set[str]:
        """Return the task ids of RUNNING jobs."""
        with self._lock:
            return {
                row[0]
                for row in self._conn.execute(
                    "SELECT task_id FROM jobs WHERE state='RUNNING'"
                )
            }
