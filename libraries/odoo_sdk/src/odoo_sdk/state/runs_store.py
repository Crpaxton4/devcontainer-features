"""The run record: ``runs.db``, the irreplaceable billing evidence.

Three tables, all STRICT, all write-once: ``runs`` (one thin row per run),
``run_envelopes`` (the opaque result envelope, kept out of the hot table) and
``run_artifacts`` (the index of a job's artifact bundle). Triggers refuse every
update and delete. ``synchronous=FULL`` because this is a billing record.
Callers use :class:`RunStore` and never see SQL.

Deliberate departures from the design's DDL (section 11):

* ``runs.job_id`` is an **unenforced correlation column**. SQLite cannot enforce
  a foreign key across two database files and the job store is designed to be
  thrown away, so the design's ``REFERENCES jobs(job_id)`` is dropped. The
  column is useful for tracing and worthless as a constraint.
* ``runs.brief`` (``TEXT NOT NULL DEFAULT ''``) is added. It is copied from the
  job row at completion so "what was actually asked for on this pull request"
  stays answerable after the job store is deleted.
* ``runs.outcome`` stores the DDL's four values; :func:`map_reported_outcome`
  owns the mapping from the agent-reported ``done | blocked | partial``.
* ``started_at``, ``ended_at`` and ``harvested_at`` are virtual generated
  columns (rule: human-readable time is never stored twice).
* ``run_envelopes`` also refuses update and delete, and ``run_artifacts`` also
  refuses delete (a write-once row whose message says append-only must not be
  deletable).

A run is keyed by a real ``session_id``. A job that failed before any session
existed produces a job row and no run row.
"""

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .dispatch_state import resolve_now_us, runs_db_path
from .migrations import Migration, open_store, transaction


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
    if not isinstance(question_count, int):
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


_RUNS_NO_UPDATE = (
    "CREATE TRIGGER {t}_no_update BEFORE UPDATE ON {t} "
    "BEGIN SELECT RAISE(ABORT, '{t} is append-only'); END"
)
_RUNS_NO_DELETE = (
    "CREATE TRIGGER {t}_no_delete BEFORE DELETE ON {t} "
    "BEGIN SELECT RAISE(ABORT, '{t} is append-only'); END"
)

RUNS_MIGRATIONS = (
    Migration(
        1,
        "create runs, run_envelopes, run_artifacts",
        (
            """
CREATE TABLE runs (
    session_id    TEXT    PRIMARY KEY,
    job_id        TEXT    NOT NULL,  -- unenforced correlation column, see module docstring
    task_id       TEXT    NOT NULL CHECK (task_id <> ''),
    repo          TEXT    NOT NULL DEFAULT '',
    branch        TEXT    NOT NULL DEFAULT '',
    pr_num        INTEGER NOT NULL DEFAULT 0 CHECK (pr_num >= 0),
    started_us    INTEGER NOT NULL CHECK (started_us > 0),
    ended_us      INTEGER NOT NULL CHECK (ended_us >= started_us),
    outcome       TEXT    NOT NULL CHECK (outcome IN
        ('COMPLETED','COMPLETED_WITH_QUESTIONS','BLOCKED','FAILED')),
    tool_version  TEXT    NOT NULL CHECK (tool_version <> ''),
    transcript    TEXT    NOT NULL DEFAULT '',
    brief         TEXT    NOT NULL DEFAULT '',
    started_at    TEXT GENERATED ALWAYS AS
        (strftime('%Y-%m-%dT%H:%M:%fZ', started_us/1000000.0, 'unixepoch')) VIRTUAL,
    ended_at      TEXT GENERATED ALWAYS AS
        (strftime('%Y-%m-%dT%H:%M:%fZ', ended_us/1000000.0, 'unixepoch')) VIRTUAL
) STRICT""",
            """
CREATE TABLE run_envelopes (
    session_id    TEXT PRIMARY KEY REFERENCES runs(session_id),
    envelope      TEXT NOT NULL CHECK (json_valid(envelope))
) STRICT""",
            """
CREATE TABLE run_artifacts (
    job_id        TEXT    PRIMARY KEY,
    session_id    TEXT    NOT NULL DEFAULT '',
    dir           TEXT    NOT NULL CHECK (dir <> ''),
    has_task_json INTEGER NOT NULL DEFAULT 0 CHECK (has_task_json IN (0,1)),
    has_session   INTEGER NOT NULL DEFAULT 0 CHECK (has_session   IN (0,1)),
    has_envelope  INTEGER NOT NULL DEFAULT 0 CHECK (has_envelope  IN (0,1)),
    bytes         INTEGER NOT NULL DEFAULT 0 CHECK (bytes >= 0),
    harvested_us  INTEGER NOT NULL CHECK (harvested_us > 0),
    missing       TEXT    NOT NULL DEFAULT '',
    harvested_at  TEXT GENERATED ALWAYS AS
        (strftime('%Y-%m-%dT%H:%M:%fZ', harvested_us/1000000.0, 'unixepoch')) VIRTUAL
) STRICT""",
            _RUNS_NO_UPDATE.format(t="runs"),
            _RUNS_NO_DELETE.format(t="runs"),
            _RUNS_NO_UPDATE.format(t="run_envelopes"),
            _RUNS_NO_DELETE.format(t="run_envelopes"),
            _RUNS_NO_UPDATE.format(t="run_artifacts"),
            _RUNS_NO_DELETE.format(t="run_artifacts"),
        ),
    ),
)

_RUN_COLUMNS = (
    "session_id, job_id, task_id, repo, branch, pr_num, started_us, ended_us, "
    "outcome, tool_version, transcript, brief, started_at, ended_at"
)
_ARTIFACT_COLUMNS = (
    "job_id, session_id, dir, has_task_json, has_session, has_envelope, bytes, "
    "harvested_us, missing, harvested_at"
)


@dataclass(frozen=True)
class RunRecord:
    """One row of ``runs``."""

    session_id: str
    job_id: str
    task_id: str
    repo: str
    branch: str
    pr_num: int
    started_us: int
    ended_us: int
    outcome: RunOutcome
    tool_version: str
    transcript: str
    brief: str
    started_at: str
    ended_at: str


@dataclass(frozen=True)
class ArtifactIndex:
    """One row of ``run_artifacts``. ``missing`` is one line per absent item."""

    job_id: str
    session_id: str
    dir: str
    has_task_json: bool
    has_session: bool
    has_envelope: bool
    bytes: int
    harvested_us: int
    missing: str
    harvested_at: str


class RunStore:
    """Typed operations over ``runs.db``. Creates and migrates it on first use."""

    def __init__(self, path: Path | None = None, *, now_us: int | None = None):
        self.path = path or runs_db_path()
        self._lock = threading.RLock()
        self._conn = open_store(
            self.path,
            RUNS_MIGRATIONS,
            synchronous="FULL",
            now_us=resolve_now_us(now_us),
        )

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock, transaction(self._conn) as conn:
            yield conn

    # -- writes ---------------------------------------------------------

    def record_run(
        self,
        *,
        session_id: str,
        job_id: str,
        task_id: str,
        started_us: int,
        ended_us: int,
        outcome: RunOutcome,
        tool_version: str,
        repo: str = "",
        branch: str = "",
        pr_num: int = 0,
        transcript: str = "",
        brief: str = "",
        envelope: str | dict | None = None,
    ) -> bool:
        """Write the run row, and the envelope row when ``envelope`` is given.

        One transaction. Returns True when written and False when ``session_id``
        was already recorded (the write is idempotent, so a retried dispatch
        cannot double-bill). ``envelope`` is a JSON string or a dict, stored
        opaquely. ``outcome`` must be a :class:`RunOutcome` value; a dead
        letter is recorded with ``RunOutcome.FAILED`` and no envelope need
        exist. Run this before artifact collection, never after.
        """
        if not session_id:
            raise ValueError("a run needs a session_id; no session means no run row")
        with self._tx() as conn:
            written = conn.execute(
                "INSERT INTO runs (session_id, job_id, task_id, repo, branch, "
                "pr_num, started_us, ended_us, outcome, tool_version, "
                "transcript, brief) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(session_id) DO NOTHING",
                (
                    session_id,
                    job_id,
                    task_id,
                    repo,
                    branch,
                    pr_num,
                    started_us,
                    ended_us,
                    RunOutcome(outcome).value,
                    tool_version,
                    transcript,
                    brief,
                ),
            ).rowcount
            if written != 1:
                return False  # only the session_id conflict is swallowed
            if envelope is not None:
                text = envelope if isinstance(envelope, str) else json.dumps(envelope)
                conn.execute(
                    "INSERT INTO run_envelopes (session_id, envelope) VALUES (?, ?)",
                    (session_id, text),
                )
        return True

    def record_artifacts(
        self,
        *,
        job_id: str,
        dir: str,
        harvested_us: int,
        session_id: str = "",
        has_task_json: bool = False,
        has_session: bool = False,
        has_envelope: bool = False,
        bytes: int = 0,
        missing: str = "",
    ) -> bool:
        """Index a job's bundle. True when written, False when already indexed."""
        with self._tx() as conn:
            written = conn.execute(
                "INSERT INTO run_artifacts (job_id, session_id, dir, "
                "has_task_json, has_session, has_envelope, bytes, "
                "harvested_us, missing) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(job_id) DO NOTHING",
                (
                    job_id,
                    session_id,
                    dir,
                    int(has_task_json),
                    int(has_session),
                    int(has_envelope),
                    bytes,
                    harvested_us,
                    missing,
                ),
            ).rowcount
        return written == 1

    # -- reads ----------------------------------------------------------

    def get_run(self, session_id: str) -> RunRecord:
        """Return the run, or raise KeyError."""
        with self._lock:
            row = self._conn.execute(
                f"SELECT {_RUN_COLUMNS} FROM runs WHERE session_id=?", (session_id,)
            ).fetchone()
        if row is None:
            raise KeyError(session_id)
        return _run_from_row(row)

    def iter_runs(
        self, *, start_us: int | None = None, end_us: int | None = None
    ) -> list[RunRecord]:
        """Return runs ordered by ``started_us`` (then session id).

        A run belongs to the half-open window ``[start_us, end_us)`` when it
        overlaps it: ``ended_us > start_us`` and ``started_us < end_us``. So a
        run (10, 20) is in [10, 20) and not in [20, 30): adjacent windows never
        both return it. A zero-length run at exactly ``start_us`` is excluded.
        Either bound may be None.
        """
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {_RUN_COLUMNS} FROM runs "
                "WHERE (?1 IS NULL OR ended_us > ?1) AND (?2 IS NULL OR started_us < ?2) "
                "ORDER BY started_us, session_id",
                (start_us, end_us),
            ).fetchall()
        return [_run_from_row(row) for row in rows]

    def get_envelope(self, session_id: str) -> dict | None:
        """Return the parsed envelope of a run, or None when none was stored."""
        with self._lock:
            row = self._conn.execute(
                "SELECT envelope FROM run_envelopes WHERE session_id=?", (session_id,)
            ).fetchone()
        return None if row is None else json.loads(row[0])

    def get_artifacts(self, job_id: str) -> ArtifactIndex | None:
        """Return the bundle index row of a job, or None."""
        with self._lock:
            row = self._conn.execute(
                f"SELECT {_ARTIFACT_COLUMNS} FROM run_artifacts WHERE job_id=?",
                (job_id,),
            ).fetchone()
        if row is None:
            return None
        values = dict(zip(row.keys(), tuple(row)))
        for flag in ("has_task_json", "has_session", "has_envelope"):
            values[flag] = bool(values[flag])
        return ArtifactIndex(**values)


def _run_from_row(row: sqlite3.Row) -> RunRecord:
    values = dict(zip(row.keys(), tuple(row)))
    values["outcome"] = RunOutcome(values["outcome"])
    return RunRecord(**values)
