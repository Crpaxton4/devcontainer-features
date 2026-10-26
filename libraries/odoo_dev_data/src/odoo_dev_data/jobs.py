"""The job store: the ``jobs`` table of ``state.db``.

The job row is the only run record. It carries the lifecycle (state, times,
dead-letter cause), the result (outcome, branch, PR, token and cost figures,
the agent envelope), the artifact bundle index and the orchestrator marks
(approved, reported, friction recorded, stop requested, question count).

Several processes write it: the dispatcher, ``dispatch submit``, the
orchestrator and, for ``question_count``, the ``task_question`` command. WAL
mode and ``busy_timeout`` (see :mod:`odoo_dev_data.db.engine`) cover the
concurrent writers. Callers use :class:`JobStore` and never see SQL.

The duplicate gate is a property of the engine, not of this code: the partial
unique index ``idx_jobs_one_live`` allows at most one PENDING or RUNNING job
per ``task_id``. :meth:`JobStore.submit` therefore just inserts and translates
the integrity error into :class:`DuplicateTaskInFlightError`. It never queries
first. Use :meth:`JobStore.reject_duplicate` to record the resulting
``DUPLICATE_TASK_IN_FLIGHT`` dead letter.

All time is integer microseconds UTC. ``jobs.outcome`` holds the *stored*
:class:`RunOutcome` of a DONE job. ``started_us`` and ``ended_us`` are the
billable window.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .db.engine import connect, resolve_now_us, transaction

_FINISHED = "('DONE','DEAD_LETTER')"
_LIVE = "('PENDING','RUNNING')"


class JobState(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DONE = "DONE"
    DEAD_LETTER = "DEAD_LETTER"


class DeadReason(str, Enum):
    """The ten dead-letter causes. Each value is a full statement."""

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


class RunOutcome(str, Enum):
    """Stored run outcomes. Each value is a complete statement."""

    COMPLETED = "COMPLETED"
    COMPLETED_WITH_QUESTIONS = "COMPLETED_WITH_QUESTIONS"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


def map_reported_outcome(reported: str | None, question_count: int = 0) -> RunOutcome:
    """Map the agent-reported outcome to the stored :class:`RunOutcome`.

    This is the one place the mapping lives; no caller may invent its own.
    The function is total: every input returns a value.

    ================  ==============  ==========================
    reported          questions       stored
    ================  ==============  ==========================
    ``done``          0               ``COMPLETED``
    ``done``          >= 1            ``COMPLETED_WITH_QUESTIONS``
    ``partial``       any             ``COMPLETED_WITH_QUESTIONS``
    ``blocked``       any             ``BLOCKED``
    anything else     any             ``FAILED``
    (incl. ``None``)
    ================  ==============  ==========================

    ``partial`` means work was done and something is left for a human to
    settle, which is what ``COMPLETED_WITH_QUESTIONS`` says. A dead-lettered
    job (no valid object) is stored as ``FAILED`` directly, and an unknown
    reported value is ``FAILED`` rather than a softer kind of success.
    """
    if isinstance(question_count, bool) or not isinstance(question_count, int):
        question_count = 0  # total: a non-int count is treated as no questions
    if reported == "done":
        return (
            RunOutcome.COMPLETED_WITH_QUESTIONS
            if question_count > 0
            else RunOutcome.COMPLETED
        )
    if reported == "partial":
        return RunOutcome.COMPLETED_WITH_QUESTIONS
    if reported == "blocked":
        return RunOutcome.BLOCKED
    return RunOutcome.FAILED


class DuplicateTaskInFlightError(Exception):
    """A live (PENDING or RUNNING) job already exists for the task."""


class InvalidJobTransitionError(Exception):
    """The job does not exist or is not in a state the operation moves from."""


@dataclass(frozen=True)
class Job:
    """One row of ``jobs``. The ``*_us`` marks are None when unset."""

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
    branch: str = ""
    pr_num: int = 0
    tool_version: str = ""
    envelope: str = ""
    transcript_path: str = ""
    bundle_dir: str = ""
    has_task_json: bool = False
    has_session: bool = False
    has_envelope: bool = False
    bytes: int = 0
    harvested_us: int | None = None
    missing: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    approved_us: int | None = None
    reported_us: int | None = None
    friction_recorded_us: int | None = None
    stop_requested_us: int | None = None
    question_count: int = 0

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Job:
        values = dict(zip(row.keys(), tuple(row)))
        values["state"] = JobState(values["state"])
        for flag in ("has_task_json", "has_session", "has_envelope"):
            values[flag] = bool(values[flag])
        return cls(**values)

    @property
    def billable_window(self) -> tuple[int, int] | None:
        """Return ``(started_us, ended_us)`` when both are set, else None."""
        if self.started_us is None or self.ended_us is None:
            return None
        return (self.started_us, self.ended_us)

    def envelope_json(self) -> dict | None:
        """Return the parsed agent envelope, or None when none was stored."""
        return json.loads(self.envelope) if self.envelope else None


class JobStore:
    """Typed operations over ``jobs`` on one held ``state.db`` connection.

    Pass ``conn`` to share an existing connection, or ``home`` to open one.
    Every method runs under one lock, so the store is safe to share across
    threads. Writes run in ``BEGIN IMMEDIATE`` transactions.
    """

    def __init__(
        self, conn: sqlite3.Connection | None = None, *, home: Path | None = None
    ) -> None:
        self._conn = conn if conn is not None else connect(home)
        self._lock = threading.RLock()

    def close(self) -> None:
        """Checkpoint the WAL (best effort) and close the connection."""
        with self._lock:
            try:
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.OperationalError:
                pass
            self._conn.close()

    def __enter__(self) -> JobStore:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock, transaction(self._conn) as conn:
            yield conn

    def _select(self, where: str, params: tuple = (), order: str = "") -> list[Job]:
        sql = f"SELECT * FROM jobs {where} {order}"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [Job.from_row(row) for row in rows]

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
        approved_us: int | None = None,
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
                    "state, brief, submitted_us, entry, approved_us) "
                    "VALUES (?, ?, ?, ?, ?, 'PENDING', ?, ?, ?, ?)",
                    (
                        job_id,
                        batch_id,
                        task_id,
                        repo,
                        round,
                        brief,
                        resolve_now_us(now_us),
                        entry,
                        approved_us,
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

        Each row holds ``job_id, batch_id, task_id, repo, brief`` and optional
        ``round``, ``entry`` and ``approved_us``. A live twin (in the store or
        earlier in ``rows``) rolls the whole batch back and raises
        DuplicateTaskInFlightError.
        """
        now = resolve_now_us(now_us)
        task_id = ""
        try:
            with self._tx() as conn:
                for row in rows:
                    task_id = row["task_id"]
                    conn.execute(
                        "INSERT INTO jobs (job_id, batch_id, task_id, repo, round, "
                        "state, brief, submitted_us, entry, approved_us) "
                        "VALUES (?, ?, ?, ?, ?, 'PENDING', ?, ?, ?, ?)",
                        (
                            row["job_id"],
                            row["batch_id"],
                            task_id,
                            row["repo"],
                            row.get("round", 1),
                            row["brief"],
                            now,
                            row.get("entry", ""),
                            row.get("approved_us"),
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

    def record_result(
        self,
        job_id: str,
        *,
        outcome: RunOutcome | str,
        session_id: str = "",
        branch: str = "",
        pr_num: int = 0,
        tool_version: str = "",
        envelope: str | dict | None = None,
        transcript_path: str = "",
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float = 0.0,
        question_count: int | None = None,
        now_us: int | None = None,
    ) -> Job:
        """RUNNING to DONE, storing the run result in the same UPDATE.

        ``outcome`` must be a :class:`RunOutcome` value. A dict ``envelope`` is
        stored as JSON and ``None`` as the empty string. An empty ``session_id``
        and a ``None`` ``question_count`` leave those columns as they are.
        """
        if isinstance(envelope, dict):
            envelope = json.dumps(envelope)
        return self._transition(
            "UPDATE jobs SET state='DONE', ended_us=?, outcome=?, "
            "session_id=CASE WHEN ?='' THEN session_id ELSE ? END, "
            "branch=?, pr_num=?, tool_version=?, envelope=?, transcript_path=?, "
            "input_tokens=?, output_tokens=?, cost_usd=?, "
            "question_count=COALESCE(?, question_count) "
            "WHERE job_id=? AND state='RUNNING'",
            (
                resolve_now_us(now_us),
                RunOutcome(outcome).value,
                session_id,
                session_id,
                branch,
                pr_num,
                tool_version,
                envelope or "",
                transcript_path,
                input_tokens,
                output_tokens,
                cost_usd,
                question_count,
                job_id,
            ),
            job_id,
            "record a result on a job that is not RUNNING",
        )

    def index_bundle(
        self,
        job_id: str,
        *,
        bundle_dir: str,
        has_task_json: bool = False,
        has_session: bool = False,
        has_envelope: bool = False,
        bytes: int = 0,
        missing: str = "",
        now_us: int | None = None,
    ) -> Job:
        """Store the artifact bundle index on a DONE or DEAD_LETTER job."""
        return self._transition(
            "UPDATE jobs SET bundle_dir=?, has_task_json=?, has_session=?, "
            "has_envelope=?, bytes=?, missing=?, harvested_us=? "
            f"WHERE job_id=? AND state IN {_FINISHED}",
            (
                bundle_dir,
                int(has_task_json),
                int(has_session),
                int(has_envelope),
                bytes,
                missing,
                resolve_now_us(now_us),
                job_id,
            ),
            job_id,
            "index a bundle on a job that is not finished",
        )

    def approve_brief(self, job_id: str, *, now_us: int | None = None) -> Job:
        """Mark the brief of a PENDING or RUNNING job as approved."""
        return self._transition(
            f"UPDATE jobs SET approved_us=? WHERE job_id=? AND state IN {_LIVE}",
            (resolve_now_us(now_us), job_id),
            job_id,
            "approve a job that is not live",
        )

    def request_stop(self, job_id: str, *, now_us: int | None = None) -> Job:
        """Record a stop request on a RUNNING job."""
        return self._transition(
            "UPDATE jobs SET stop_requested_us=? WHERE job_id=? AND state='RUNNING'",
            (resolve_now_us(now_us), job_id),
            job_id,
            "stop a job that is not RUNNING",
        )

    def increment_questions(self, job_id: str) -> Job:
        """Add one to the question count of a RUNNING job."""
        return self._transition(
            "UPDATE jobs SET question_count=question_count+1 "
            "WHERE job_id=? AND state='RUNNING'",
            (job_id,),
            job_id,
            "count a question on a job that is not RUNNING",
        )

    def _mark(self, column: str, job_ids: Iterable[str], now_us: int | None) -> int:
        now = resolve_now_us(now_us)
        changed = 0
        with self._tx() as conn:
            for job_id in job_ids:
                changed += conn.execute(
                    f"UPDATE jobs SET {column}=? WHERE job_id=? AND {column} IS NULL",
                    (now, job_id),
                ).rowcount
        return changed

    def mark_reported(
        self, job_ids: Iterable[str], *, now_us: int | None = None
    ) -> int:
        """Set ``reported_us`` where it is NULL; return the rows changed."""
        return self._mark("reported_us", job_ids, now_us)

    def mark_friction_recorded(
        self, job_ids: Iterable[str], *, now_us: int | None = None
    ) -> int:
        """Set ``friction_recorded_us`` where it is NULL; return the rows changed."""
        return self._mark("friction_recorded_us", job_ids, now_us)

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
            f"detail=? WHERE job_id=? AND state IN {_LIVE}",
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
        jobs = self._select("WHERE job_id=?", (job_id,))
        if not jobs:
            raise KeyError(job_id)
        return jobs[0]

    def list_by_state(self, state: JobState) -> list[Job]:
        """Return jobs in ``state``, oldest submission first."""
        return self._select(
            "WHERE state=?",
            (JobState(state).value,),
            "ORDER BY submitted_us, job_id",
        )

    def list_by_batch(self, batch_id: str) -> list[Job]:
        """Return the jobs of one batch in insertion order (empty if unknown)."""
        return self._select("WHERE batch_id=?", (batch_id,), "ORDER BY rowid")

    def dead_letters(self) -> list[Job]:
        """Return the dead letter list the orchestrator reads."""
        return self.list_by_state(JobState.DEAD_LETTER)

    def count_live(self, repo: str) -> int:
        """Count PENDING plus RUNNING jobs of ``repo`` (the container idle test)."""
        with self._lock:
            return self._conn.execute(
                f"SELECT COUNT(*) FROM jobs WHERE repo=? AND state IN {_LIVE}",
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

    def list_batches_pending_report(self) -> list[str]:
        """Return batches that are all finished with a job not yet reported.

        Ordered by the earliest ``submitted_us`` of the batch.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT batch_id FROM jobs GROUP BY batch_id "
                f"HAVING SUM(state NOT IN {_FINISHED}) = 0 "
                "AND SUM(reported_us IS NULL) > 0 "
                "ORDER BY MIN(submitted_us), batch_id"
            ).fetchall()
        return [row[0] for row in rows]

    def list_finished(
        self, *, start_us: int | None = None, end_us: int | None = None
    ) -> list[Job]:
        """Return finished jobs that ran, ordered by ``started_us`` then job id.

        A job is in window ``[start_us, end_us)`` when ``started_us < end_us``
        and either ``ended_us > start_us`` or it is zero-length
        (``started_us == ended_us``) with ``started_us >= start_us``; so
        adjacent windows never both return a job and no job is in none.
        Either bound may be None.
        """
        return self._select(
            f"WHERE state IN {_FINISHED} AND started_us IS NOT NULL "
            "AND (?1 IS NULL OR ended_us > ?1 "
            "OR (started_us = ended_us AND started_us >= ?1)) "
            "AND (?2 IS NULL OR started_us < ?2)",
            (start_us, end_us),
            "ORDER BY started_us, job_id",
        )
