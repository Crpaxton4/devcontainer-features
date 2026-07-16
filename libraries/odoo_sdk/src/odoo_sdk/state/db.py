"""SQLite-backed FSM state for task time-tracking sessions."""

import json
import os
import sqlite3
import subprocess
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from odoo_sdk._utils import as_utc

from .models import (
    EventRecord,
    InvalidStateTransitionError,
    SessionWindow,
    TaskAlreadyRunningError,
    TaskNotRunningError,
    TaskRun,
    TaskState,
    TrackerStateMissingError,
)

#: Filename of the single central tracker database under the state root (#369).
#: There is exactly one host-provisioned DB per user — events, ``task_runs``, and
#: the upload ledger all live in it — so ``repo`` is an ordinary column keyed on
#: the normalized ``owner/repo`` label rather than a per-repo directory hash.
TRACKER_DB_FILENAME = "tracker.db"

# Repo-less agent events cannot key on a real repository, so they are grouped
# under this reserved sentinel. It is a valid, stable group key (never a real
# ``owner/repo``) so such events still sessionize deterministically in the
# SQL-derived read path (:meth:`LocalStateClient.derive_sessions_overlapping`).
AGENTLESS_REPO_SENTINEL = "\x00agent"

# Max ids bound into a single ``... IN (...)`` statement (delete and series-assign).
# Chunked to stay well under SQLite's historical 999-variable limit; each caller
# runs all chunks inside one transaction so the whole operation stays atomic.
_ID_CHUNK = 500


def _chunks(seq: Sequence[int], size: int = _ID_CHUNK):
    """Yield successive ``size``-length slices of ``seq``."""
    for start in range(0, len(seq), size):
        yield seq[start : start + size]


def _default_root() -> Path:
    """Resolve the user-writable base directory for tracker state.

    Precedence: ``$XDG_STATE_HOME/odoo-task-tracker`` when ``XDG_STATE_HOME``
    is set, otherwise ``~/.local/state/odoo-task-tracker``.
    """
    xdg_state_home = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg_state_home) if xdg_state_home else Path.home() / ".local" / "state"
    return base / "odoo-task-tracker"


# Canonical schema for the central tracker DB — the ONE authoritative DDL, applied
# only by the host provisioning step (``scripts/init_tracker_db.py``, invoked by
# ``setup.sh`` / ``setup.ps1``) and by :func:`create_schema`, never on connection
# open (#369; see :class:`TrackerStateMissingError`). The stdlib-only init script
# embeds a verbatim copy of this DDL (it cannot import the SDK on the host); an SDK
# parity test asserts the two produce an identical ``sqlite_master`` so they never
# drift.
#
# It carries EVERY column and index the pre-#369 per-repo DBs accumulated across
# their migrations — ``task_runs.aborted_at`` (#356), ``events.external_id`` with
# its partial unique dedupe index (resync), ``idx_events_timestamp`` (#359), and
# the ``session_uploads`` ``task_id``/``started_at``/``ended_at`` orphan-discovery
# columns (#353) — so a freshly provisioned DB is schema-identical to a migrated
# one; there is no migration tooling. Every statement is ``IF NOT EXISTS`` so
# provisioning is idempotent. Sessions are still derived from ``events`` at query
# time (see ``_DERIVE_SESSIONS_SQL``); there is no materialized ``sessions`` table.
SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS task_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id      INTEGER NOT NULL,
    task_name    TEXT    NOT NULL,
    project_id   INTEGER NOT NULL,
    project_name TEXT    NOT NULL,
    state        TEXT    NOT NULL CHECK(state IN ('RUNNING', 'AWAITING_ANSWERS', 'STOPPED')),
    started_at   TEXT    NOT NULL,
    stopped_at   TEXT,
    timesheet_id INTEGER,
    notes        TEXT    NOT NULL DEFAULT '[]',
    aborted_at   TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT    NOT NULL,
    timestamp   TEXT    NOT NULL,
    task_ids    TEXT    NOT NULL DEFAULT '[]',
    repo        TEXT    NOT NULL DEFAULT '',
    pr_num      INTEGER NOT NULL DEFAULT 0,
    branch      TEXT    NOT NULL DEFAULT '',
    subject     TEXT    NOT NULL DEFAULT '',
    payload     TEXT,
    external_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events (timestamp);

CREATE UNIQUE INDEX IF NOT EXISTS idx_events_external_id
    ON events(external_id) WHERE external_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS session_uploads (
    session_key  TEXT PRIMARY KEY,
    timesheet_id INTEGER NOT NULL,
    hours        REAL NOT NULL,
    uploaded_at  TEXT NOT NULL,
    task_id      TEXT,
    started_at   TEXT,
    ended_at     TEXT
);
"""


def create_schema(conn: sqlite3.Connection) -> None:
    """Apply :data:`SCHEMA_DDL` to ``conn`` (host provisioning / tests only).

    The ONLY sanctioned way to bring a tracker DB into existence, called from
    exactly two places: the host-side ``scripts/init_tracker_db.py`` (which embeds
    an identical DDL copy for stdlib-only host use) and the SDK test suite's shared
    fixture. It is NEVER called on connection open — the SDK consumes a
    host-provisioned DB and refuses to self-create one (#369; see
    :class:`TrackerStateMissingError`). Idempotent: every statement is ``IF NOT EXISTS``.
    """
    conn.executescript(SCHEMA_DDL)


# Development-family sources: ``commit``, ``agent``, ``chatter`` resync events,
# the ``calendar`` meeting ticks and ``email`` sent-mail point events (#370), plus
# the open-ended ``claude:<HookName>`` family (EventType.CLAUDE_HOOK). Calendar
# ticks are synthetic point events emitted 5 min apart across a meeting so the
# UNCHANGED gap derivation reconstructs the meeting as one session (#370); a sent
# email is a lone point event that picks up the #355 minimum like a commit. A
# derived group containing ANY development-family event is labeled "Development".
_DEVELOPMENT_SOURCE_PREDICATE = (
    "(source IN ('commit', 'agent', 'chatter', 'calendar', 'email') "
    "OR source LIKE 'claude:%')"
)

# Review-family sources (#378 item 6): submitted PR ``review`` passes and authored
# PR/issue ``comment`` events (``gh:comment:<id>``). Review activity is *bursty* —
# e.g. one pass of 33 inline comments — which the Python ETL's ``FixedDurationStrategy``
# over-bills (15 min × 33 = 8.25 h) and a lone long pass under-bills; gap-windowing
# models both correctly (the 33-comment burst becomes one ~2 h session, a lone
# review a single-event session that floors to the #355 minimum). A group with ONLY
# review-family events is labeled "Review" (development wins a mixed group). ``merge``
# stays deliberately OUT of the windowed derivation — it is a point-in-time release
# marker billed by the fixed/audit strategy, not a work span.
_REVIEW_SOURCE_PREDICATE = "source IN ('review', 'comment')"

# Sources whose events participate in gap-based sessionization: the union of the
# development and review families. ``merge`` is the only ingested source excluded.
_SESSION_SOURCE_PREDICATE = (
    f"({_DEVELOPMENT_SOURCE_PREDICATE} OR {_REVIEW_SOURCE_PREDICATE})"
)


# CTE that reproduces the legacy gap-based sessionization directly over ``events``
# at query time. The inactivity gap is bound at execution (SQLite views cannot
# take a parameter), so zero materialization/staleness exists for any producer.
# The consecutive-event delta is ``ROUND``ed to whole seconds before the gap
# comparison: ``julianday`` arithmetic carries a float epsilon that would
# otherwise make two events *exactly* ``gap_secs`` apart read as > the gap and
# spuriously split. Event timestamps are second-resolution for sessionization, so
# rounding is exact at the boundary and matches the legacy ``total_seconds()`` cut.
#
# Partitioning (#352): sessions partition by ``task_key`` ALONE — the repo is no
# longer a partition key. Agent/hook/MCP events carry ``repo=""`` (the sentinel)
# while resync commit/chatter events carry the real ``owner/repo`` label; keying
# on repo split one task's span into two parallel lanes and billed it twice. Repo
# survives as display metadata: ``COALESCE(MAX(NULLIF(repo,'')), :sentinel)`` per
# group prefers the real label and falls back to the repo-less sentinel. Distinct
# *tasks* still partition separately, so genuine concurrent tasks bill in parallel.
#
# Window prefilter (#359): the ``base`` CTE bounds ``timestamp`` to the queried
# ``[start, end]`` widened by :func:`_derivation_margin` each side, so a TUI
# refresh scans a slice via ``idx_events_timestamp`` instead of sessionizing every
# event ever recorded. The margin (``max(gap_secs, 1 day)``) is wide enough that a
# session merely straddling the queried window still pulls in its neighbours whole;
# a session whose total span exceeds the margin can in theory be clipped at the
# far edge (documented, accepted tradeoff — no fixed margin bounds a gap chain).
#
# Fan-out (#362): a multi-task event (``--attach-active-run`` attaches EVERY
# active run's task id, so a single hook event can carry ``task_ids=[t1, t2]``)
# is fanned out over its task ids with ``json_each``: it yields one ``base`` row
# per task id and thus extends BOTH tasks' sessions instead of only the first.
# ``events.id`` is qualified because ``json_each`` also exposes an ``id`` column.
# ``DISTINCT`` collapses a task id that appears twice in one array so a duplicated
# id can never double-count an event within its own session. One event id can now
# anchor two tasks' sessions; keys stay distinct because the session key is
# ``task|min_event_id`` (task-scoped).
#
# Category (#378 item 6): review-family sources (``review``/``comment``) now form
# WINDOWED sessions alongside the development family, so the derivation must label
# each session so the TUI can distinguish "Review" from "Development". Each ``base``
# row carries an ``is_dev`` flag (1 for a development-family source, 0 for a
# review-family one); ``MAX(is_dev)`` per group is the label decision — a group with
# ANY development-family event is "Development" (development wins a mixed task's
# label), a group of purely review-family events is "Review". The flag is derived
# from ``source`` alone, so it does not perturb the ``DISTINCT`` (one id → one row).
#
# Task attribution (#409): the ``json_array_length(events.task_ids) > 0`` filter
# below — with ``COALESCE(NULLIF(value, ''), 'UNKNOWN')`` bucketing empty ids — is
# the CANONICAL attribution predicate. The diagnostic gap-sweep mirrors the same
# "non-empty ``task_ids``" rule in ``sessionization/transform.py``
# (``billable_events``); parity is pinned by ``test_sessionization/test_parity.py``.
_DERIVE_SESSIONS_SQL = f"""
WITH base AS (
    SELECT DISTINCT
           events.id AS id, events.timestamp AS timestamp,
           events.pr_num AS pr_num, events.repo AS repo,
           julianday(events.timestamp) AS jd,
           CASE WHEN {_DEVELOPMENT_SOURCE_PREDICATE} THEN 1 ELSE 0 END AS is_dev,
           COALESCE(NULLIF(task_each.value, ''), 'UNKNOWN') AS task_key
    FROM events, json_each(events.task_ids) AS task_each
    WHERE {_SESSION_SOURCE_PREDICATE}
      AND json_array_length(events.task_ids) > 0
      AND events.timestamp >= :wstart
      AND events.timestamp <= :wend
),
marked AS (
    SELECT *,
           CASE WHEN LAG(jd) OVER w IS NULL
                  OR ROUND((jd - LAG(jd) OVER w) * 86400.0) > :gap_secs
                THEN 1 ELSE 0 END AS is_start
    FROM base
    WINDOW w AS (PARTITION BY task_key ORDER BY jd, id)
),
numbered AS (
    SELECT *,
           SUM(is_start) OVER (PARTITION BY task_key
                               ORDER BY jd, id ROWS UNBOUNDED PRECEDING) AS session_num
    FROM marked
)
SELECT task_key,
       COALESCE(MAX(NULLIF(repo, '')), :sentinel) AS repo_display,
       MIN(id)            AS session_key_id,
       MIN(timestamp)     AS started_at,
       MAX(timestamp)     AS ended_at,
       MAX(pr_num)        AS pr_num,
       MAX(is_dev)        AS has_dev,
       json_group_array(id) AS event_ids
FROM numbered
GROUP BY task_key, session_num
HAVING started_at <= :end AND ended_at >= :start
{{extra}}
ORDER BY started_at
"""


def _resolve_state_root() -> Path:
    """Resolve the tracker state root, honoring the same env overrides.

    Precedence: ``ODOO_TASK_TRACKER_DIR`` (highest) then the XDG-aware
    :func:`_default_root`. This is the single resolver both self-resolved
    ``LocalStateClient`` construction and cross-project discovery share, so the
    directory a DB is written under is exactly the one discovery scans.
    """
    override = os.environ.get("ODOO_TASK_TRACKER_DIR")
    return Path(override) if override else _default_root()


def _derive_repo_label(remote_url: str) -> str:
    """Return the ``owner/repo`` label for a git remote URL.

    Strips a trailing ``.git`` and keeps the last two path segments so both ssh
    (``git@github.com:owner/repo.git``) and https
    (``https://github.com/owner/repo.git``) forms collapse to ``owner/repo``.
    Falls back to the cleaned URL when it has fewer than two segments.
    """
    cleaned = remote_url.strip()
    if cleaned.endswith(".git"):
        cleaned = cleaned[:-4]
    # Treat ``:`` (scp-like ssh separator, scheme ``://``) as a path separator so
    # both URL shapes split into the same segment list.
    segments = [seg for seg in cleaned.replace(":", "/").split("/") if seg]
    if len(segments) >= 2:
        return "/".join(segments[-2:])
    return cleaned or "(unknown)"


def tracker_db_path(root: Optional[Path] = None) -> Path:
    """Return the path to the single central tracker DB (#369).

    ``<state-root>/tracker.db``, where the state root is ``root`` when given, else
    the env-aware :func:`_resolve_state_root` (``ODOO_TASK_TRACKER_DIR`` → XDG).
    No git remote is consulted and no directory is created (#369): the location is
    fixed and the DB's existence is the host's responsibility, not the SDK's.
    """
    base = Path(root) if root is not None else _resolve_state_root()
    return base / TRACKER_DB_FILENAME


def current_repo_label() -> str:
    """Return the normalized ``owner/repo`` label for the cwd's git remote, or ''.

    Best-effort display metadata for events written from a working tree (#369):
    the repo no longer selects the database (there is one central DB), so a
    non-git cwd or a missing remote is not an error — it simply yields ``""``,
    which sessionizes under the repo-less sentinel. ssh and https clones of one
    repo converge because :func:`_derive_repo_label` normalizes both URL shapes
    to the same ``owner/repo``.
    """
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return ""
    label = _derive_repo_label(result.stdout.strip())
    return "" if label == "(unknown)" else label


# Columns selected for every task_run read, in _parse_run order.
_TASK_RUN_COLUMNS = (
    "id, task_id, task_name, project_id, project_name, state, "
    "started_at, stopped_at, timesheet_id, notes, aborted_at"
)


def _parse_run(row: tuple) -> TaskRun:
    (
        id_,
        task_id,
        task_name,
        project_id,
        project_name,
        state,
        started_at,
        stopped_at,
        timesheet_id,
        notes_json,
        aborted_at,
    ) = row
    return TaskRun(
        id=id_,
        task_id=task_id,
        task_name=task_name,
        project_id=project_id,
        project_name=project_name,
        state=TaskState(state),
        started_at=datetime.fromisoformat(started_at),
        stopped_at=datetime.fromisoformat(stopped_at) if stopped_at else None,
        timesheet_id=timesheet_id,
        notes=json.loads(notes_json),
        aborted_at=datetime.fromisoformat(aborted_at) if aborted_at else None,
    )


# Columns selected for every event read, in EventRecord field order.
_EVENT_COLUMNS = (
    "id, source, timestamp, task_ids, repo, pr_num, branch, subject, payload, "
    "external_id"
)


def _normalize_utc_isoformat(ts: datetime) -> str:
    """Return ``ts`` as a uniform UTC isoformat string, used for stored values and bounds.

    The SQL-derived read path compares ``MIN/MAX(timestamp)`` and query bounds as
    *strings*, which is only correct when every value shares one UTC offset. An aware
    timestamp is converted to UTC; a naive one — a stored value, the TUI's
    ``datetime.combine(date, time.min)`` window edge, or the query layer's
    ``datetime.min``/``datetime.max`` sentinel — is treated as already-UTC and
    stamped with ``+00:00``, so all rows and bounds sort and compare uniformly (the
    naive sentinels still sort past every real row).
    """
    return as_utc(ts).isoformat()


def _derivation_margin(gap_secs: int) -> timedelta:
    """Return how far the derivation prefilter widens the queried window (#359).

    A gap-based session is a chain of events each at most ``gap_secs`` apart, so
    no fixed margin can guarantee a session's *whole* span is captured. We widen
    by ``max(gap_secs, 1 day)`` each side: one full inactivity gap keeps an event
    sitting exactly on the boundary chained to its neighbour, and one day covers a
    typical work session that merely straddles the window. A session whose total
    span exceeds this margin can be clipped at its far edge — an accepted tradeoff
    for not sessionizing the entire events table on every refresh.
    """
    return timedelta(seconds=max(gap_secs, 86_400))


def _widen(ts: datetime, delta: timedelta) -> datetime:
    """Return ``ts + delta``, clamped to ``datetime.min``/``max`` on over/underflow.

    The query layer passes ``datetime.min``/``datetime.max`` for an unbounded edge,
    and shifting those overflows; clamping by the sign of ``delta`` keeps the widened
    bound a valid, still-past-everything sentinel.
    """
    try:
        return ts + delta
    except OverflowError:
        return datetime.min if delta < timedelta(0) else datetime.max


def _window_where(
    start: Optional[datetime],
    end: Optional[datetime],
    extra_clauses: Sequence[str] = (),
) -> tuple[str, list[str]]:
    """Build the optional ``[start, end)`` timestamp WHERE clause and its params.

    Bounds are half-open (``>= start``, ``< end``) and normalized to the uniform
    stored-timestamp string form. ``extra_clauses`` are ANDed ahead of the bounds
    (e.g. an unattributed-events filter that always yields a WHERE).
    """
    clauses = list(extra_clauses)
    params: list[str] = []
    if start is not None:
        clauses.append("timestamp >= ?")
        params.append(_normalize_utc_isoformat(start))
    if end is not None:
        clauses.append("timestamp < ?")
        params.append(_normalize_utc_isoformat(end))
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, params


def _parse_event(row: tuple) -> EventRecord:
    (id_, source, ts, task_ids, repo, pr_num, branch, subject, payload, ext_id) = row
    return EventRecord(
        id=id_,
        source=source,
        timestamp=datetime.fromisoformat(ts),
        task_ids=json.loads(task_ids),
        repo=repo,
        pr_num=pr_num,
        branch=branch,
        subject=subject,
        payload=json.loads(payload) if payload else None,
        external_id=ext_id,
    )


def _parse_session_upload(row: tuple) -> dict:
    """Shape a ``session_uploads`` row into the accessor's dict.

    ``task_id``/``started_at``/``ended_at`` are ``None`` for legacy rows written
    before #353 added the orphan-discovery columns.
    """
    return {
        "session_key": row[0],
        "timesheet_id": row[1],
        "hours": row[2],
        "uploaded_at": row[3],
        "task_id": row[4],
        "started_at": row[5],
        "ended_at": row[6],
    }


def _parse_derived_window(row: tuple) -> SessionWindow:
    """Build a :class:`SessionWindow` from a ``derive_sessions_overlapping`` row.

    The derived row carries ``(task_key, repo_display, session_key_id,
    started_at, ended_at, pr_num, has_dev, event_ids)`` where ``event_ids`` is a
    JSON array. ``repo_display`` is display-only metadata (#352): the group's real
    ``owner/repo`` label when any event carried one, else the repo-less sentinel.
    ``id`` is the session's minimum event id (stable under append-only tail writes).

    ``has_dev`` labels the window (#378 item 6): ``1`` when the group holds any
    development-family event → ``development`` / ``Development`` (development wins
    a mixed task's label); ``0`` when the group is purely review-family
    (``review``/``comment``) → ``review`` / ``Review`` so the TUI can badge it.

    ``event_ids`` is sorted ascending in Python: ``json_group_array`` has no
    order guarantee (SQLite < 3.44 rejects an aggregate ``ORDER BY``), so sorting
    by id — which is monotonic with insertion — gives a deterministic order for
    the bulk event fetch instead of relying on the group-scan order.
    """
    (task_key, repo_display, session_key_id, started, ended, pr_num, has_dev,
     event_ids_json) = row
    strategy_name, category = ("development", "Development") if has_dev else (
        "review", "Review"
    )
    return SessionWindow(
        id=session_key_id,
        task_id=task_key,
        repo=repo_display,
        started_at=datetime.fromisoformat(started),
        ended_at=datetime.fromisoformat(ended),
        strategy_name=strategy_name,
        category=category,
        pr_num=pr_num,
        event_ids=tuple(sorted(json.loads(event_ids_json))),
    )


class LocalStateClient:
    """SQLite-backed state store for task tracking sessions."""

    def __init__(self, db_path: Optional[Path] = None):
        """Bind to a tracker DB path WITHOUT touching the filesystem (#369).

        Construction is inert — no git remote, directory, or schema work. A missing
        DB is not detected until the first :meth:`_connect`, which raises
        :class:`TrackerStateMissingError`. ``db_path`` defaults to the single central
        :func:`tracker_db_path`; tests and callers pass an explicit path.
        """
        self._db_path = Path(db_path) if db_path is not None else tracker_db_path()

    def _connect(self) -> sqlite3.Connection:
        # The DB is host-provisioned; the SDK NEVER creates it (#369; see
        # :class:`TrackerStateMissingError`). ``mode=rw`` raises rather than creating
        # a missing file; the explicit existence check turns that into the single
        # named error every entry point surfaces.
        if not self._db_path.exists():
            raise TrackerStateMissingError(
                f"No tracker database at {self._db_path}. This database is "
                "provisioned on the host and bind-mounted into the container; it is "
                "not created automatically. Run setup.sh on the host, then rebuild "
                "the container."
            )
        conn = sqlite3.connect(f"file:{self._db_path}?mode=rw", uri=True)
        # WAL lets a writer and readers proceed concurrently, and a 2s busy
        # timeout makes a second writer wait for the lock instead of failing
        # instantly with "database is locked". With one central DB now taking
        # cross-container writers (the claude-event-hook shim, MCP
        # _emit_tool_event, the TUI), these are load-bearing rather than optional
        # (#357). WAL is a persistent property of the DB file (set by the host
        # provisioning step and re-asserted here); the busy timeout is
        # per-connection and so must be set on every connect. WAL works on a
        # Docker bind mount on Linux (Docker Desktop's gRPC-FUSE share is the only
        # environment where it can misbehave — not our containers-on-Linux case).
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=2000")
        # Foreign-key enforcement is intentionally left at SQLite's default (off).
        # The current schema declares no foreign keys.
        return conn

    def _select_runs(self, where: str, params: tuple = ()) -> list[TaskRun]:
        """Run ``SELECT {_TASK_RUN_COLUMNS} FROM task_runs {where}`` and parse rows."""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_TASK_RUN_COLUMNS} FROM task_runs {where}", params
            ).fetchall()
        return [_parse_run(row) for row in rows]

    def _select_run(self, where: str, params: tuple) -> Optional[TaskRun]:
        """Return the single matching task_run, or None."""
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {_TASK_RUN_COLUMNS} FROM task_runs {where}", params
            ).fetchone()
        return _parse_run(row) if row else None

    def _require_active_run(self, task_id: int) -> TaskRun:
        """Return the active run for ``task_id`` or raise :class:`TaskNotRunningError`."""
        run = self.get_active_run(task_id)
        if run is None:
            raise TaskNotRunningError(f"No active session found for task {task_id}.")
        return run

    def get_active_run(self, task_id: int) -> Optional[TaskRun]:
        return self._select_run(
            "WHERE task_id = ? AND state IN ('RUNNING', 'AWAITING_ANSWERS')",
            (task_id,),
        )

    def get_run_by_id(self, run_id: int) -> Optional[TaskRun]:
        return self._select_run("WHERE id = ?", (run_id,))

    def get_all_active_runs(self) -> list[TaskRun]:
        return self._select_runs(
            "WHERE state IN ('RUNNING', 'AWAITING_ANSWERS') ORDER BY started_at"
        )

    def get_all_runs(self) -> list[TaskRun]:
        return self._select_runs("ORDER BY started_at")

    def distinct_task_ids(self) -> list[int]:
        """Return the distinct task ids on record in ``task_runs``, ascending.

        The set of Odoo tasks this project has ever tracked. The chatter resync
        puller uses it to scope its ``mail.message`` search to only the tasks the
        SDK actually knows about, rather than scanning every task in Odoo.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT task_id FROM task_runs ORDER BY task_id"
            ).fetchall()
        return [int(row[0]) for row in rows]

    def get_stopped_runs_with_timesheet(self) -> list[TaskRun]:
        return self._select_runs(
            "WHERE state = 'STOPPED' AND timesheet_id IS NOT NULL ORDER BY started_at"
        )

    def create_run(
        self,
        task_id: int,
        task_name: str,
        project_id: int,
        project_name: str,
        timesheet_id: Optional[int] = None,
    ) -> TaskRun:
        existing = self.get_active_run(task_id)
        if existing is not None:
            raise TaskAlreadyRunningError(
                f"Task {task_id!r} ({task_name!r}) already has an active session "
                f"(id={existing.id}, state={existing.state.value})."
            )
        started_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO task_runs (task_id, task_name, project_id, project_name, "
                "state, started_at, timesheet_id, notes) VALUES (?, ?, ?, ?, 'RUNNING', ?, ?, '[]')",
                (task_id, task_name, project_id, project_name, started_at, timesheet_id),
            )
            run_id = cursor.lastrowid
        return self.get_run_by_id(run_id)  # type: ignore[return-value]

    def transition_to_awaiting(self, task_id: int) -> TaskRun:
        run = self._require_active_run(task_id)
        # get_active_run only returns RUNNING/AWAITING_ANSWERS rows, so every active
        # run may transition to AWAITING_ANSWERS; the guard cannot fire.
        assert run.state in (TaskState.RUNNING, TaskState.AWAITING_ANSWERS)
        with self._connect() as conn:
            conn.execute(
                "UPDATE task_runs SET state = 'AWAITING_ANSWERS' WHERE id = ?",
                (run.id,),
            )
        return self.get_run_by_id(run.id)  # type: ignore[return-value]

    def transition_to_running(self, task_id: int) -> TaskRun:
        run = self._require_active_run(task_id)
        if run.state != TaskState.AWAITING_ANSWERS:
            raise InvalidStateTransitionError(
                f"Cannot resume task {task_id}: expected AWAITING_ANSWERS, "
                f"got {run.state.value}."
            )
        with self._connect() as conn:
            conn.execute(
                "UPDATE task_runs SET state = 'RUNNING' WHERE id = ?",
                (run.id,),
            )
        return self.get_run_by_id(run.id)  # type: ignore[return-value]

    def stop_run(self, task_id: int, timesheet_id: Optional[int] = None) -> TaskRun:
        run = self._require_active_run(task_id)
        stopped_at = datetime.now(timezone.utc).isoformat()
        tid = timesheet_id if timesheet_id is not None else run.timesheet_id
        with self._connect() as conn:
            conn.execute(
                "UPDATE task_runs SET state = 'STOPPED', stopped_at = ?, timesheet_id = ? "
                "WHERE id = ?",
                (stopped_at, tid, run.id),
            )
        return self.get_run_by_id(run.id)  # type: ignore[return-value]

    def abort_run(self, task_id: int) -> TaskRun:
        """Force-close the active run to STOPPED and stamp ``aborted_at`` (#356).

        The abort analog of :meth:`stop_run`: it moves the active run straight to
        ``STOPPED`` *and* records the abort instant in the additive ``aborted_at``
        column so the upload path can exclude the run's leftover sessions. The
        stamp is taken as ``now`` — at or after the abort-dispatch agent event
        that lands at abort time — so the aborted window covers that event and it
        can never re-derive a billable session. ``stop_run`` is left
        billing-neutral (a normal stop must still bill), which is why the abort
        stamp lives on a distinct method rather than a flag on ``stop_run``.

        :raises TaskNotRunningError: When there is no active run to abort.
        """
        run = self._require_active_run(task_id)
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "UPDATE task_runs SET state = 'STOPPED', stopped_at = ?, "
                "aborted_at = ? WHERE id = ?",
                (now, now, run.id),
            )
        return self.get_run_by_id(run.id)  # type: ignore[return-value]

    def latest_event_timestamp_for_task(self, task_id: int) -> Optional[datetime]:
        """Return the most recent event timestamp attributed to ``task_id``, or None.

        The staleness clock for the reaper (#366): a run's "last activity" is the
        latest event carrying its task id — the same ``task_ids`` array the
        derivation and ``--attach-active-run`` write to. Events fan out over their
        task ids with ``json_each`` (a hook event can carry several), so a task
        matches whenever it appears anywhere in the array. Timestamps are stored as
        one uniform UTC isoformat, so a string ``MAX`` is the true chronological
        maximum. The task id is bound as text because ``task_ids`` holds string ids.
        Returns ``None`` when the task has no events on record.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT MAX(events.timestamp) "
                "FROM events, json_each(events.task_ids) AS task_each "
                "WHERE task_each.value = ?",
                (str(task_id),),
            ).fetchone()
        return datetime.fromisoformat(row[0]) if row and row[0] is not None else None

    def get_aborted_runs(self) -> list[TaskRun]:
        """Return every aborted run (``aborted_at`` stamped), ordered by start.

        The upload path (#356) skips any derived session lying wholly within an
        aborted run's ``[started_at, aborted_at]`` window for the matching task,
        so an aborted run's leftover events never bill. Work done on the same task
        after a fresh ``start_task`` falls in a *later* run window and still bills.
        """
        return self._select_runs("WHERE aborted_at IS NOT NULL ORDER BY started_at")

    def update_timesheet_id(self, run_id: int, timesheet_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE task_runs SET timesheet_id = ? WHERE id = ?",
                (timesheet_id, run_id),
            )

    def append_note(self, task_id: int, note: str) -> None:
        run = self.get_active_run(task_id)
        if run is None:
            raise TaskNotRunningError(f"No active session for task {task_id}.")
        updated_notes = json.dumps(run.notes + [note])
        with self._connect() as conn:
            conn.execute(
                "UPDATE task_runs SET notes = ? WHERE id = ?",
                (updated_notes, run.id),
            )

    def remap_timesheet_id(self, old_timesheet_id: int, new_timesheet_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE task_runs SET timesheet_id = ? WHERE timesheet_id = ?",
                (new_timesheet_id, old_timesheet_id),
            )

    # ── Unified event/session model (additive; alongside the FSM store) ──────

    def _insert_event_row(
        self, conn: sqlite3.Connection, event: EventRecord
    ) -> sqlite3.Cursor:
        """Insert one ``events`` row, deduping when ``external_id`` is set.

        Externally-keyed events use ``INSERT OR IGNORE`` so a re-ingested id is a
        no-op against the ``events(external_id)`` partial unique index; events
        with no external id use a plain ``INSERT`` so a genuine constraint
        violation still surfaces rather than being silently swallowed.
        """
        verb = "INSERT OR IGNORE" if event.external_id is not None else "INSERT"
        return conn.execute(
            f"{verb} INTO events (source, timestamp, task_ids, repo, pr_num, "
            "branch, subject, payload, external_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event.source,
                _normalize_utc_isoformat(event.timestamp),
                json.dumps(event.task_ids),
                event.repo,
                event.pr_num,
                event.branch,
                event.subject,
                json.dumps(event.payload) if event.payload is not None else None,
                event.external_id,
            ),
        )

    def add_event(self, event: EventRecord) -> EventRecord:
        """Insert one event and return the stored row.

        Idempotent for externally-keyed events: when ``event.external_id`` is
        already present the insert is ignored and the existing row is returned, so
        callers never see a duplicate. Use :meth:`add_event_dedup` when you need
        to know whether a new row was actually written (e.g. to count inserts).
        """
        with self._connect() as conn:
            cursor = self._insert_event_row(conn, event)
            if cursor.rowcount == 1:
                event_id = cursor.lastrowid
            else:
                event_id = conn.execute(
                    "SELECT id FROM events WHERE external_id = ?",
                    (event.external_id,),
                ).fetchone()[0]
        return self.get_event(event_id)  # type: ignore[return-value]

    def add_event_dedup(self, event: EventRecord) -> bool:
        """Insert an externally-keyed event; return True iff a new row was written.

        The idempotency primitive the resync pullers count on: a first ingest of
        an ``external_id`` returns ``True`` (row inserted); any re-ingest of the
        same id returns ``False`` (``INSERT OR IGNORE`` matched the partial unique
        index and did nothing), so a puller can report exactly how many events it
        added.
        """
        with self._connect() as conn:
            return self._insert_event_row(conn, event).rowcount == 1

    def get_event(self, event_id: int) -> Optional[EventRecord]:
        """Return one event by id, or None."""
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {_EVENT_COLUMNS} FROM events WHERE id = ?",
                (event_id,),
            ).fetchone()
        return _parse_event(row) if row else None

    def last_note_at(self, task_id: int) -> Optional[datetime]:
        """Return the timestamp of the most recent recorded ``task_note`` for a task.

        Reads the append-only ``events`` timeseries for the newest
        ``source='agent'`` event whose subject is ``task_note`` and whose
        ``task_ids`` include this task, fanning out the JSON array with
        ``json_each`` so a multi-task event still matches. Returns ``None`` when
        the task has no recorded note event yet. This is the read primitive the
        checkpoint-cadence hint (#387) derives elapsed time from; it never writes
        and is safe to call on every note/start.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT MAX(events.timestamp) FROM events, "
                "json_each(events.task_ids) AS task_each "
                "WHERE events.source = 'agent' AND events.subject = 'task_note' "
                "AND task_each.value = ?",
                (str(task_id),),
            ).fetchone()
        ts = row[0] if row is not None else None
        return datetime.fromisoformat(ts) if ts else None

    def get_events(
        self,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> list[EventRecord]:
        """Return events ordered by timestamp, optionally bounded by range."""
        where, params = _window_where(start, end)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_EVENT_COLUMNS} FROM events{where} ORDER BY timestamp",
                tuple(params),
            ).fetchall()
        return [_parse_event(r) for r in rows]

    def get_unattributed_events(
        self,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> list[EventRecord]:
        """Return events carrying NO task ids (``task_ids=[]``) in ``[start, end)``.

        The read half of the TUI triage surface (#370, acceptance item 9). An
        event ingested with an empty ``task_ids`` array is invisible to billing —
        the derivation requires ``json_array_length(task_ids) > 0`` — so such an
        event silently never bills unless it is surfaced for triage. This returns
        every unattributed event in the window regardless of ``source``: triage
        must see all ingested-but-unattributed events (calendar meetings, emails,
        diagnostics), not only the sources that would sessionize, so the session
        source predicate is deliberately NOT applied. Ordered by timestamp; the
        window bounds are half-open (``>= start``, ``< end``) to match
        :meth:`get_events` and :meth:`count_events`.
        """
        where, params = _window_where(start, end, ("json_array_length(task_ids) = 0",))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_EVENT_COLUMNS} FROM events{where} ORDER BY timestamp",
                tuple(params),
            ).fetchall()
        return [_parse_event(r) for r in rows]

    def assign_event_task_ids(self, event_ids: list[int], task_id: int) -> int:
        """Attribute every listed event to ``task_id`` in ONE transaction (#370).

        The write half of the triage surface: it sets ``task_ids`` to
        ``[str(task_id)]`` on all ``event_ids`` so a whole calendar series (each
        tick a separate event sharing a ``gcal:<id>:tick:`` external-id prefix) is
        attributed atomically by a single call. Once written the events satisfy
        ``json_array_length(task_ids) > 0`` and immediately become derivable, so
        the meeting bills instead of being silently dropped.

        All chunks execute inside one ``self._connect()`` transaction, so a series
        assignment is all-or-nothing — a failure part-way never leaves half a
        series attributed. Chunking only bounds the per-statement variable count.

        :raises ValueError: When ``task_id`` is not a positive integer. No Odoo
            round-trip validates the id exists — triage only guarantees a
            well-formed, positive task id, not a live one.
        """
        if isinstance(task_id, bool) or not isinstance(task_id, int) or task_id <= 0:
            raise ValueError(f"task_id must be a positive integer, got {task_id!r}")
        if not event_ids:
            return 0
        payload = json.dumps([str(task_id)])
        updated = 0
        with self._connect() as conn:
            for chunk in _chunks(event_ids):
                placeholders = ",".join("?" for _ in chunk)
                cursor = conn.execute(
                    f"UPDATE events SET task_ids = ? WHERE id IN ({placeholders})",
                    (payload, *chunk),
                )
                updated += cursor.rowcount
        return updated

    def derive_sessions_overlapping(
        self,
        start: datetime,
        end: datetime,
        *,
        gap_secs: int,
        task_id: Optional[str] = None,
        repo: Optional[str] = None,
    ) -> list[SessionWindow]:
        """Derive whole sessions overlapping ``[start, end]`` directly from events.

        Gap-based sessionization is computed in one CTE over ``events`` at query
        time (the gap is bound at execution, so nothing materializes or goes stale).
        A session is a maximal run of a single *task*'s events at most ``gap_secs``
        apart, returned whole (its true global bounds), never clipped. The mechanics
        — task-only partitioning (#352), the window prefilter (#359), multi-task
        fan-out (#362), and the development/review labeling (#378 item 6) — are
        documented in full on :data:`_DERIVE_SESSIONS_SQL`.

        **Intentional behavior delta:** events carrying *no* task ids (e.g. most
        MCP-wrapper dispatch events) are stored as diagnostics but NEVER form a
        session — they are filtered out (``json_array_length(task_ids) > 0``).

        ``start``/``end`` bound the overlap window (inclusive). ``task_id`` restricts
        to one task id (any id an event carries, since a multi-task event contributes
        to each). ``repo`` restricts to one *display* repo — a group's real
        ``owner/repo`` when any event carried one, else :data:`AGENTLESS_REPO_SENTINEL`
        (pass the sentinel to select purely repo-less sessions). Results are ordered
        by start time.
        """
        margin = _derivation_margin(gap_secs)
        params: dict[str, object] = {
            "sentinel": AGENTLESS_REPO_SENTINEL,
            "gap_secs": gap_secs,
            "start": _normalize_utc_isoformat(start),
            "end": _normalize_utc_isoformat(end),
            "wstart": _normalize_utc_isoformat(_widen(start, -margin)),
            "wend": _normalize_utc_isoformat(_widen(end, margin)),
        }
        extra = ""
        if task_id is not None:
            extra += " AND task_key = :task_id"
            params["task_id"] = task_id
        if repo is not None:
            # Repo is post-aggregation display metadata (#352), so the filter
            # matches the same COALESCE(MAX(...)) display expression the SELECT
            # projects, applied in the HAVING alongside the overlap predicate.
            extra += " AND COALESCE(MAX(NULLIF(repo, '')), :sentinel) = :repo"
            params["repo"] = repo
        sql = _DERIVE_SESSIONS_SQL.format(extra=extra)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_parse_derived_window(r) for r in rows]

    def get_events_by_ids(self, ids: list[int]) -> list[EventRecord]:
        """Return the events with the given ids, in the order requested.

        A bulk fetch used by the derived read path to embed a session's events.
        Ids with no matching row are silently skipped; the returned order mirrors
        ``ids`` (not the table order) so a session's events stay in derivation
        order.
        """
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_EVENT_COLUMNS} FROM events WHERE id IN ({placeholders})",
                tuple(ids),
            ).fetchall()
        by_id = {row[0]: _parse_event(row) for row in rows}
        return [by_id[i] for i in ids if i in by_id]

    def count_events(
        self,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> int:
        """Return the number of events, optionally bounded by ``[start, end)``."""
        where, params = _window_where(start, end)
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) FROM events{where}", tuple(params)
            ).fetchone()
        return int(row[0])

    def event_ids_before(self, cutoff: datetime) -> list[int]:
        """Return the ids of every event strictly older than ``cutoff``, ascending.

        The retention read primitive (#363): the ``prune`` planner needs the full
        set of aged event ids — including untargeted diagnostic events that never
        form a session — so it can subtract the ids it must protect and delete the
        remainder. ``cutoff`` is normalized to the same uniform UTC isoformat the
        stored timestamps use, so the ``timestamp < ?`` string comparison is exact.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id FROM events WHERE timestamp < ? ORDER BY id",
                (_normalize_utc_isoformat(cutoff),),
            ).fetchall()
        return [int(row[0]) for row in rows]

    def delete_events(self, ids: list[int]) -> int:
        """Delete the events with the given ids and return the number removed.

        The retention write primitive (#363): the sole event-DELETION path in the
        SDK. Ids are deleted in bounded chunks so a heavy day's worth of ids never
        exceeds SQLite's per-statement variable limit. This is a raw delete with no
        guard of its own — the ``prune`` planner is responsible for only ever
        handing it ids it has proven safe to remove (see
        :func:`odoo_sdk.prune.plan_prune`).
        """
        if not ids:
            return 0
        deleted = 0
        with self._connect() as conn:
            for chunk in _chunks(ids):
                placeholders = ",".join("?" for _ in chunk)
                cursor = conn.execute(
                    f"DELETE FROM events WHERE id IN ({placeholders})", tuple(chunk)
                )
                deleted += cursor.rowcount
        return deleted

    def vacuum(self) -> None:
        """Reclaim free pages left by a prune via a full ``VACUUM``.

        A trivial, best-effort space reclaim (#363): after a real prune deletes
        aged rows their pages sit on the freelist until reused, so a one-shot
        ``VACUUM`` rewrites the (small, ephemeral) local DB to hand the space back
        to the filesystem. Run on its own connection so no open transaction can
        make SQLite reject the statement. Reclaim is non-essential (the prune has
        already committed), so a lock held by a concurrent writer — VACUUM needs an
        exclusive lock — is swallowed rather than turned into a spurious failure;
        the busy timeout gives that writer a chance to drain first.

        This never creates the DB (#369): a ``VACUUM`` on a missing file would
        materialize an empty one, so an absent DB is a best-effort no-op (and
        ``mode=rw`` refuses to create one anyway).
        """
        if not self._db_path.exists():
            return
        conn = sqlite3.connect(
            f"file:{self._db_path}?mode=rw", uri=True, isolation_level=None
        )
        conn.execute("PRAGMA busy_timeout=2000")
        try:
            conn.execute("VACUUM")
        except sqlite3.OperationalError:
            pass
        finally:
            conn.close()

    def get_session_upload(self, session_key: str) -> Optional[dict]:
        """Return the recorded upload for a derived session key, or None.

        The mapping is the idempotency record for per-session timesheet uploads:
        it ties a session's stable key to the single ``account.analytic.line`` id
        it was reconciled onto.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT session_key, timesheet_id, hours, uploaded_at, "
                "task_id, started_at, ended_at "
                "FROM session_uploads WHERE session_key = ?",
                (session_key,),
            ).fetchone()
        return _parse_session_upload(row) if row is not None else None

    def list_session_uploads(self) -> list[dict]:
        """Return every recorded upload mapping (for the orphan sweep, #353).

        The upload sweep diffs these against the set of currently-derived session
        keys for a window: a mapping whose recorded window overlaps the queried
        window but no longer derives has been merged away, so its Odoo row must be
        zeroed and the mapping retired. Local DBs are small and ephemeral, so the
        whole ledger is returned and filtered in Python rather than in SQL.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT session_key, timesheet_id, hours, uploaded_at, "
                "task_id, started_at, ended_at FROM session_uploads"
            ).fetchall()
        return [_parse_session_upload(row) for row in rows]

    def delete_session_upload(self, session_key: str) -> None:
        """Retire a mapping from the ledger once its Odoo row has been zeroed.

        The SDK never deletes Odoo records (see ``forbid_unlink``), but the local
        idempotency ledger is not an Odoo record — an orphaned mapping whose row
        the sweep has zeroed is removed so it is never re-swept.
        """
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM session_uploads WHERE session_key = ?", (session_key,)
            )

    def record_session_upload(
        self,
        session_key: str,
        timesheet_id: int,
        hours: float,
        *,
        task_id: Optional[str] = None,
        started_at: Optional[datetime] = None,
        ended_at: Optional[datetime] = None,
    ) -> None:
        """Upsert the upload mapping for a derived session key.

        Idempotent: re-recording the same key overwrites the mapped timesheet id,
        hours, timestamp, and the ``task_id``/window bounds the orphan sweep keys
        on. The bounds are normalized to the uniform UTC isoformat stored
        timestamps use, so the sweep can string-compare them against a window.
        """
        uploaded_at = datetime.now(timezone.utc).isoformat()
        started = _normalize_utc_isoformat(started_at) if started_at is not None else None
        ended = _normalize_utc_isoformat(ended_at) if ended_at is not None else None
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO session_uploads (session_key, timesheet_id, hours, "
                "uploaded_at, task_id, started_at, ended_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(session_key) DO UPDATE SET "
                "timesheet_id = excluded.timesheet_id, hours = excluded.hours, "
                "uploaded_at = excluded.uploaded_at, task_id = excluded.task_id, "
                "started_at = excluded.started_at, ended_at = excluded.ended_at",
                (session_key, timesheet_id, hours, uploaded_at, task_id, started, ended),
            )

    def get_setting(self, key: str) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
        return row[0] if row else None

    def set_setting(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
