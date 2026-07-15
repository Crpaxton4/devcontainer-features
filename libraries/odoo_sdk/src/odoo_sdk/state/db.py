"""SQLite-backed FSM state for task time-tracking sessions."""

import hashlib
import json
import os
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .models import (
    EventRecord,
    InvalidStateTransitionError,
    ProjectIdError,
    SessionWindow,
    TaskAlreadyRunningError,
    TaskNotRunningError,
    TaskRun,
    TaskState,
)

# Repo-less agent events cannot key on a real repository, so they are grouped
# under this reserved sentinel. It is a valid, stable group key (never a real
# ``owner/repo``) so such events still sessionize deterministically in the
# SQL-derived read path (:meth:`LocalStateClient.derive_sessions_overlapping`).
AGENTLESS_REPO_SENTINEL = "\x00agent"


def _default_root() -> Path:
    """Resolve the user-writable base directory for tracker state.

    Precedence: ``$XDG_STATE_HOME/odoo-task-tracker`` when ``XDG_STATE_HOME``
    is set, otherwise ``~/.local/state/odoo-task-tracker``.
    """
    xdg_state_home = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg_state_home) if xdg_state_home else Path.home() / ".local" / "state"
    return base / "odoo-task-tracker"


_SCHEMA = """
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
    notes        TEXT    NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


# Unified event timeseries for the sessionization ETL. This is additive and
# lives alongside the ``task_runs`` FSM above; it never modifies it. Sessions are
# derived from these events at query time (see ``_DERIVE_SESSIONS_SQL``); there is
# no materialized ``sessions`` table, so nothing here can go stale.
_SESSIONIZATION_SCHEMA = """
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

-- Idempotency ledger for the SQL-derived per-session timesheet uploads. Each
-- derived session's stable key maps to the single account.analytic.line row it
-- was reconciled onto, so a re-upload rewrites that row rather than duplicating.
CREATE TABLE IF NOT EXISTS session_uploads (
    session_key  TEXT PRIMARY KEY,
    timesheet_id INTEGER NOT NULL,
    hours        REAL NOT NULL,
    uploaded_at  TEXT NOT NULL
);
"""


# Sources whose events participate in gap-based sessionization. Matches the
# development-strategy sources: ``commit``, ``agent``, future ``chatter`` resync
# events, plus the open-ended ``claude:<HookName>`` family (EventType.CLAUDE_HOOK).
# ``merge`` / ``review`` are fixed-strategy sources and never form sessions here.
_SESSION_SOURCE_PREDICATE = (
    "(source IN ('commit', 'agent', 'chatter') OR source LIKE 'claude:%')"
)


# CTE that reproduces the legacy gap-based sessionization directly over ``events``
# at query time. The inactivity gap is bound at execution (SQLite views cannot
# take a parameter), so zero materialization/staleness exists for any producer.
# The consecutive-event delta is ``ROUND``ed to whole seconds before the gap
# comparison: ``julianday`` arithmetic carries a float epsilon that would
# otherwise make two events *exactly* ``gap_secs`` apart read as > the gap and
# spuriously split. Event timestamps are second-resolution for sessionization, so
# rounding is exact at the boundary and matches the legacy ``total_seconds()`` cut.
_DERIVE_SESSIONS_SQL = f"""
WITH base AS (
    SELECT id, timestamp, pr_num,
           julianday(timestamp) AS jd,
           COALESCE(NULLIF(json_extract(task_ids, '$[0]'), ''), 'UNKNOWN') AS task_key,
           CASE WHEN repo = '' THEN :sentinel ELSE repo END AS repo_key
    FROM events
    WHERE {_SESSION_SOURCE_PREDICATE}
      AND json_array_length(task_ids) > 0
),
marked AS (
    SELECT *,
           CASE WHEN LAG(jd) OVER w IS NULL
                  OR ROUND((jd - LAG(jd) OVER w) * 86400.0) > :gap_secs
                THEN 1 ELSE 0 END AS is_start
    FROM base
    WINDOW w AS (PARTITION BY task_key, repo_key ORDER BY jd, id)
),
numbered AS (
    SELECT *,
           SUM(is_start) OVER (PARTITION BY task_key, repo_key
                               ORDER BY jd, id ROWS UNBOUNDED PRECEDING) AS session_num
    FROM marked
)
SELECT task_key, repo_key,
       MIN(id)            AS session_key_id,
       MIN(timestamp)     AS started_at,
       MAX(timestamp)     AS ended_at,
       MAX(pr_num)        AS pr_num,
       json_group_array(id) AS event_ids
FROM numbered
GROUP BY task_key, repo_key, session_num
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


def _resolve_project_identity() -> tuple[Path, str]:
    """Return the ``(project_dir, remote_url)`` for the current git working tree.

    The project dir is ``<state-root>/<sha256(remote)[:16]>`` (created if
    absent); the remote URL is returned alongside so callers can persist the
    repo identity into the DB itself, curing the "orphaned DB keyed by an opaque
    hash" problem (#331).

    :raises ProjectIdError: When no git remote ``origin`` can be resolved.
    """
    root = _resolve_state_root()
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=True,
        )
        remote_url = result.stdout.strip()
    except subprocess.CalledProcessError:
        raise ProjectIdError(
            "Could not determine project ID: no git remote 'origin' found. "
            "Ensure the working directory is in a git repository with a remote."
        )
    project_hash = hashlib.sha256(remote_url.encode()).hexdigest()[:16]
    project_dir = root / project_hash
    project_dir.mkdir(parents=True, exist_ok=True)
    return project_dir, remote_url


def _get_project_dir() -> Path:
    """Return only the resolved project dir (identity discarded)."""
    project_dir, _ = _resolve_project_identity()
    return project_dir


def _migrate_drop_materialized_sessions(conn: sqlite3.Connection) -> None:
    """Drop the legacy materialized ``sessions`` table if present.

    Sessions are now derived from ``events`` at query time, so the materialized
    ``sessions`` table (and the event → session link that fed it) is dead. This
    drops the table on any DB created before the change. Idempotent:
    ``DROP TABLE IF EXISTS`` is a no-op once the table is gone.

    The orphaned ``events.session_id`` column is deliberately left in place on
    old DBs: dropping it needs ``ALTER TABLE DROP COLUMN`` (SQLite ≥ 3.35) and
    the column is never written nor selected. Crucially it also carries a legacy
    ``REFERENCES sessions`` foreign key baked into the ``events`` CREATE
    statement; enforcing that FK after the parent table is gone would make every
    ``events`` INSERT fail. Foreign-key enforcement is therefore left off (see
    :meth:`LocalStateClient._connect`) — the current schema declares no foreign
    keys at all — so the stale column and its dangling reference are wholly
    inert.
    """
    conn.execute("DROP TABLE IF EXISTS sessions")


def _migrate_events_external_id(conn: sqlite3.Connection) -> None:
    """Add the ``events.external_id`` dedupe column and its partial unique index.

    The resync pullers key each ingested event on a stable external identity
    (``git:<sha>``, ``gh:pr:<n>``, ``odoo:mail:<id>``) so a re-run never
    duplicates. Databases created before resync lack the column; this adds it via
    ``ALTER TABLE`` (guarded by a ``PRAGMA table_info`` probe, since SQLite has no
    ``ADD COLUMN IF NOT EXISTS``) and creates the ``WHERE external_id IS NOT NULL``
    partial unique index that enforces the ``INSERT OR IGNORE`` dedupe.

    Idempotent: the column is added only when absent, and the index uses
    ``IF NOT EXISTS``. New DBs already carry the column from
    :data:`_SESSIONIZATION_SCHEMA`, so the ``ALTER`` is skipped for them.
    """
    columns = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
    if "external_id" not in columns:
        conn.execute("ALTER TABLE events ADD COLUMN external_id TEXT")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_events_external_id "
        "ON events(external_id) WHERE external_id IS NOT NULL"
    )


def _migrate_task_sessions_to_task_runs(conn: sqlite3.Connection) -> None:
    """Rename a legacy ``task_sessions`` FSM table to ``task_runs`` in place.

    The FSM "session" concept was renamed to "run" to free ``session_id`` for
    the event-derived time-sessions. Databases created before the rename carry a
    ``task_sessions`` table; this preserves their rows by renaming the table
    rather than letting ``CREATE TABLE IF NOT EXISTS task_runs`` create an empty
    one alongside it. It must run BEFORE the schema script so the rename happens
    before ``task_runs`` would otherwise be created. Idempotent: it only renames
    when ``task_sessions`` exists and ``task_runs`` does not.
    """
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "task_sessions" in tables and "task_runs" not in tables:
        conn.execute("ALTER TABLE task_sessions RENAME TO task_runs")


# Columns selected for every task_run read, in _parse_run order.
_TASK_RUN_COLUMNS = (
    "id, task_id, task_name, project_id, project_name, state, "
    "started_at, stopped_at, timesheet_id, notes"
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
    )


# Columns selected for every event read, in EventRecord field order.
_EVENT_COLUMNS = (
    "id, source, timestamp, task_ids, repo, pr_num, branch, subject, payload, "
    "external_id"
)


def _normalize_utc_isoformat(ts: datetime) -> str:
    """Return ``ts`` as a uniform UTC isoformat string.

    The SQL-derived read path compares ``MIN/MAX(timestamp)`` as *strings*, which
    is only correct when every stored timestamp shares one UTC offset. An aware
    timestamp is converted to UTC; a naive one is treated as already-UTC and
    stamped with ``+00:00`` so all rows sort and compare uniformly.
    """
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc).isoformat()
    return ts.astimezone(timezone.utc).isoformat()


def _bound_isoformat(ts: datetime) -> str:
    """Return a query bound as an isoformat string comparable to stored rows.

    Aware bounds are normalized to UTC to match the uniform-UTC stored strings;
    naive bounds (``datetime.min``/``datetime.max`` sentinels from the query
    layer) are passed through unchanged so open-ended ranges keep working.
    """
    return _normalize_utc_isoformat(ts) if ts.tzinfo else ts.isoformat()


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


def _parse_derived_window(row: tuple) -> SessionWindow:
    """Build a :class:`SessionWindow` from a ``derive_sessions_overlapping`` row.

    The derived row carries ``(task_key, repo_key, session_key_id, started_at,
    ended_at, pr_num, event_ids)`` where ``event_ids`` is a JSON array. Derived
    windows are always the ``development`` strategy; ``id`` is the session's
    minimum event id (stable under append-only tail writes).

    ``event_ids`` is sorted ascending in Python: ``json_group_array`` has no
    order guarantee (SQLite < 3.44 rejects an aggregate ``ORDER BY``), so sorting
    by id — which is monotonic with insertion — gives a deterministic order for
    the bulk event fetch instead of relying on the group-scan order.
    """
    (task_key, repo_key, session_key_id, started, ended, pr_num, event_ids_json) = row
    return SessionWindow(
        id=session_key_id,
        task_id=task_key,
        repo=repo_key,
        started_at=datetime.fromisoformat(started),
        ended_at=datetime.fromisoformat(ended),
        strategy_name="development",
        category="Development",
        pr_num=pr_num,
        event_ids=tuple(sorted(json.loads(event_ids_json))),
    )


class LocalStateClient:
    """SQLite-backed state store for task tracking sessions."""

    def __init__(self, db_path: Optional[Path] = None):
        remote_url: Optional[str] = None
        if db_path is None:
            project_dir, remote_url = _resolve_project_identity()
            db_path = project_dir / "tasks.db"
        self._db_path = db_path
        self._init_schema()
        # Only stamp identity when we resolved the project ourselves; an injected
        # db_path (tests, discovery, cross-DB abort) must never be mutated with a
        # remote it did not come from.
        if remote_url is not None:
            self._persist_identity(remote_url)

    def _persist_identity(self, remote_url: str) -> None:
        """Record ``repo_remote_url``/``repo_label`` once; never overwrite.

        Written exactly once per DB on the first self-resolved construction so a
        DB carries the human-readable identity of the repo it belongs to. Existing
        values are left untouched, so re-opening a project never clobbers an
        identity already on record.
        """
        if self.get_setting("repo_remote_url") is None:
            self.set_setting("repo_remote_url", remote_url)
        if self.get_setting("repo_label") is None:
            self.set_setting("repo_label", _derive_repo_label(remote_url))

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        # WAL lets a writer and readers proceed concurrently, and a 2s busy
        # timeout makes a second writer wait for the lock instead of failing
        # instantly with "database is locked". Without these, concurrent writers
        # (the claude-event-hook shim, MCP _emit_tool_event, the TUI) hit an
        # immediate lock and silently drop the event (hook `|| true`, MCP
        # try/except pass). WAL is a persistent property of the DB file; the
        # busy timeout is per-connection and so must be set on every connect.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=2000")
        # Foreign-key enforcement is intentionally left at SQLite's default (off).
        # The current schema declares no foreign keys, and legacy DBs still carry
        # an orphaned ``events.session_id REFERENCES sessions`` column; enforcing
        # that dangling FK after the migration drops ``sessions`` would break
        # every ``events`` INSERT (see _migrate_drop_materialized_sessions).
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            _migrate_task_sessions_to_task_runs(conn)
            conn.executescript(_SCHEMA)
            conn.executescript(_SESSIONIZATION_SCHEMA)
            _migrate_drop_materialized_sessions(conn)
            _migrate_events_external_id(conn)

    def get_active_run(self, task_id: int) -> Optional[TaskRun]:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {_TASK_RUN_COLUMNS} "
                "FROM task_runs WHERE task_id = ? AND state IN ('RUNNING', 'AWAITING_ANSWERS')",
                (task_id,),
            ).fetchone()
        return _parse_run(tuple(row)) if row else None

    def get_run_by_id(self, run_id: int) -> Optional[TaskRun]:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {_TASK_RUN_COLUMNS} "
                "FROM task_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
        return _parse_run(tuple(row)) if row else None

    def get_all_active_runs(self) -> list[TaskRun]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_TASK_RUN_COLUMNS} "
                "FROM task_runs WHERE state IN ('RUNNING', 'AWAITING_ANSWERS') "
                "ORDER BY started_at"
            ).fetchall()
        return [_parse_run(tuple(r)) for r in rows]

    def get_all_runs(self) -> list[TaskRun]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_TASK_RUN_COLUMNS} "
                "FROM task_runs ORDER BY started_at"
            ).fetchall()
        return [_parse_run(tuple(r)) for r in rows]

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
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_TASK_RUN_COLUMNS} "
                "FROM task_runs WHERE state = 'STOPPED' AND timesheet_id IS NOT NULL "
                "ORDER BY started_at"
            ).fetchall()
        return [_parse_run(tuple(r)) for r in rows]

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
        run = self.get_active_run(task_id)
        if run is None:
            raise TaskNotRunningError(
                f"No active session found for task {task_id}."
            )
        if run.state not in (TaskState.RUNNING, TaskState.AWAITING_ANSWERS):
            raise InvalidStateTransitionError(
                f"Cannot transition task {task_id} to AWAITING_ANSWERS from {run.state.value}."
            )
        with self._connect() as conn:
            conn.execute(
                "UPDATE task_runs SET state = 'AWAITING_ANSWERS' WHERE id = ?",
                (run.id,),
            )
        return self.get_run_by_id(run.id)  # type: ignore[return-value]

    def transition_to_running(self, task_id: int) -> TaskRun:
        run = self.get_active_run(task_id)
        if run is None:
            raise TaskNotRunningError(
                f"No active session found for task {task_id}."
            )
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
        run = self.get_active_run(task_id)
        if run is None:
            raise TaskNotRunningError(
                f"No active session found for task {task_id}."
            )
        stopped_at = datetime.now(timezone.utc).isoformat()
        tid = timesheet_id if timesheet_id is not None else run.timesheet_id
        with self._connect() as conn:
            conn.execute(
                "UPDATE task_runs SET state = 'STOPPED', stopped_at = ?, timesheet_id = ? "
                "WHERE id = ?",
                (stopped_at, tid, run.id),
            )
        return self.get_run_by_id(run.id)  # type: ignore[return-value]

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
        return _parse_event(tuple(row)) if row else None

    def get_events(
        self,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> list[EventRecord]:
        """Return events ordered by timestamp, optionally bounded by range."""
        clauses: list[str] = []
        params: list[str] = []
        if start is not None:
            clauses.append("timestamp >= ?")
            params.append(start.isoformat())
        if end is not None:
            clauses.append("timestamp < ?")
            params.append(end.isoformat())
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_EVENT_COLUMNS} FROM events{where} ORDER BY timestamp",
                tuple(params),
            ).fetchall()
        return [_parse_event(tuple(r)) for r in rows]

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

        Gap-based sessionization is computed in one CTE over the ``events`` table
        at query time — the inactivity gap is bound at execution — so there is no
        materialized ``sessions`` table to go stale for any producer. A session is
        a maximal run of a ``(task, repo)`` group's events where every consecutive
        pair is at most ``gap_secs`` apart; it is returned whole (its true global
        bounds), never clipped, so cross-day and cross-range sessions read
        identically through any overlapping window.

        Only development-strategy sources participate (``commit``/``agent``/
        ``chatter`` and the ``claude:<HookName>`` family); ``merge``/``review`` are
        excluded. Every derived window is ``strategy_name='development'`` /
        ``category='Development'``.

        **Intentional behavior delta:** events carrying *no* task ids (e.g. most
        MCP-wrapper dispatch events) are stored as diagnostics but NEVER form a
        session — they are filtered out (``json_array_length(task_ids) > 0``).

        :param start: Inclusive lower bound of the overlap window.
        :param end: Inclusive upper bound of the overlap window.
        :param gap_secs: The fixed inactivity gap in seconds.
        :param task_id: Restrict to one task id (the group's first task id), or None.
        :param repo: Restrict to one repo key, or None. Repo-less events group
            under :data:`AGENTLESS_REPO_SENTINEL`; pass it to select those.
        :return: Overlapping derived windows ordered by start time.
        """
        params: dict[str, object] = {
            "sentinel": AGENTLESS_REPO_SENTINEL,
            "gap_secs": gap_secs,
            "start": _bound_isoformat(start),
            "end": _bound_isoformat(end),
        }
        extra = ""
        if task_id is not None:
            extra += " AND task_key = :task_id"
            params["task_id"] = task_id
        if repo is not None:
            extra += " AND repo_key = :repo"
            params["repo"] = repo
        sql = _DERIVE_SESSIONS_SQL.format(extra=extra)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_parse_derived_window(tuple(r)) for r in rows]

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
        by_id = {row[0]: _parse_event(tuple(row)) for row in rows}
        return [by_id[i] for i in ids if i in by_id]

    def count_events(
        self,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> int:
        """Return the number of events, optionally bounded by ``[start, end)``."""
        clauses: list[str] = []
        params: list[str] = []
        if start is not None:
            clauses.append("timestamp >= ?")
            params.append(_bound_isoformat(start))
        if end is not None:
            clauses.append("timestamp < ?")
            params.append(_bound_isoformat(end))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) FROM events{where}", tuple(params)
            ).fetchone()
        return int(row[0])

    def get_session_upload(self, session_key: str) -> Optional[dict]:
        """Return the recorded upload for a derived session key, or None.

        The mapping is the idempotency record for per-session timesheet uploads:
        it ties a session's stable key to the single ``account.analytic.line`` id
        it was reconciled onto.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT session_key, timesheet_id, hours, uploaded_at "
                "FROM session_uploads WHERE session_key = ?",
                (session_key,),
            ).fetchone()
        if row is None:
            return None
        return {
            "session_key": row[0],
            "timesheet_id": row[1],
            "hours": row[2],
            "uploaded_at": row[3],
        }

    def record_session_upload(
        self, session_key: str, timesheet_id: int, hours: float
    ) -> None:
        """Upsert the upload mapping for a derived session key.

        Idempotent: re-recording the same key overwrites the mapped timesheet id,
        hours, and timestamp rather than inserting a second row.
        """
        uploaded_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO session_uploads (session_key, timesheet_id, hours, uploaded_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(session_key) DO UPDATE SET "
                "timesheet_id = excluded.timesheet_id, hours = excluded.hours, "
                "uploaded_at = excluded.uploaded_at",
                (session_key, timesheet_id, hours, uploaded_at),
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


# Backwards-compatible alias: the local state store was historically named
# ``TaskStateDB``. Keep the old name importable so existing callers and tests
# that reference ``TaskStateDB`` continue to work.
TaskStateDB = LocalStateClient
