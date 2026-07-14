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


# Unified event/session model for the sessionization ETL. This is additive and
# lives alongside the ``task_runs`` FSM above; it never modifies it.
_SESSIONIZATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
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

CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events (timestamp);

CREATE TABLE IF NOT EXISTS sessions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id       TEXT    NOT NULL,
    repo          TEXT    NOT NULL DEFAULT '',
    started_at    TEXT    NOT NULL,
    ended_at      TEXT    NOT NULL,
    strategy_name TEXT    NOT NULL DEFAULT 'development',
    category      TEXT    NOT NULL DEFAULT 'Development',
    pr_num        INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_sessions_started_at ON sessions (started_at);
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


def _migrate_events_session_id(conn: sqlite3.Connection) -> None:
    """Ensure ``events.session_id`` and its index exist on any DB.

    New databases get the column from the CREATE TABLE above; this guarded
    ``ALTER TABLE`` adds it to databases created before the event to session
    link existed. Both steps are idempotent. The index is created here (not in
    the schema script) so it is only built after the column is guaranteed to
    exist, whether the DB is new or migrated.
    """
    columns = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
    if "session_id" not in columns:
        conn.execute("ALTER TABLE events ADD COLUMN session_id INTEGER")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_events_session_id ON events (session_id)"
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
    "id, source, timestamp, task_ids, repo, pr_num, branch, subject, "
    "payload, session_id"
)


def _parse_event(row: tuple) -> EventRecord:
    (id_, source, ts, task_ids, repo, pr_num, branch, subject, payload, session_id) = row
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
        session_id=session_id,
    )


# Columns selected for every session-window read, in SessionWindow field order.
_SESSION_COLUMNS = (
    "id, task_id, repo, started_at, ended_at, strategy_name, category, pr_num"
)


def _parse_session_window(row: tuple) -> SessionWindow:
    (id_, task_id, repo, started, ended, strategy, category, pr_num) = row
    return SessionWindow(
        id=id_,
        task_id=task_id,
        repo=repo,
        started_at=datetime.fromisoformat(started),
        ended_at=datetime.fromisoformat(ended),
        strategy_name=strategy,
        category=category,
        pr_num=pr_num,
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
        # Enforce the events.session_id -> sessions.id FK (ON DELETE SET NULL)
        # so deleting a session nulls its links rather than leaving orphans.
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            _migrate_task_sessions_to_task_runs(conn)
            conn.executescript(_SCHEMA)
            conn.executescript(_SESSIONIZATION_SCHEMA)
            _migrate_events_session_id(conn)

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

    def add_event(self, event: EventRecord) -> EventRecord:
        """Insert one event into the unified ``events`` timeseries table."""
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO events (source, timestamp, task_ids, repo, pr_num, "
                "branch, subject, payload, session_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event.source,
                    event.timestamp.isoformat(),
                    json.dumps(event.task_ids),
                    event.repo,
                    event.pr_num,
                    event.branch,
                    event.subject,
                    json.dumps(event.payload) if event.payload is not None else None,
                    event.session_id,
                ),
            )
            event_id = cursor.lastrowid
        return self.get_event(event_id)  # type: ignore[return-value]

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

    def set_event_session(self, event_id: int, session_id: Optional[int]) -> None:
        """Set (or clear) the ``session_id`` link for one event.

        Passing ``None`` unlinks the event. This is the primitive the
        incremental sessionizer's link deltas are applied through.
        """
        with self._connect() as conn:
            conn.execute(
                "UPDATE events SET session_id = ? WHERE id = ?",
                (session_id, event_id),
            )

    def get_events_for_session(self, session_id: int) -> list[EventRecord]:
        """Return the events linked to one session, ordered by timestamp."""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_EVENT_COLUMNS} FROM events WHERE session_id = ? "
                "ORDER BY timestamp",
                (session_id,),
            ).fetchall()
        return [_parse_event(tuple(r)) for r in rows]

    def add_session_window(self, window: SessionWindow) -> SessionWindow:
        """Insert one computed session window into the ``sessions`` table."""
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO sessions (task_id, repo, started_at, ended_at, "
                "strategy_name, category, pr_num) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    window.task_id,
                    window.repo,
                    window.started_at.isoformat(),
                    window.ended_at.isoformat(),
                    window.strategy_name,
                    window.category,
                    window.pr_num,
                ),
            )
            window_id = cursor.lastrowid
        return self.get_session_window(window_id)  # type: ignore[return-value]

    def get_session_window(self, window_id: int) -> Optional[SessionWindow]:
        """Return one session window by id, or None."""
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {_SESSION_COLUMNS} FROM sessions WHERE id = ?",
                (window_id,),
            ).fetchone()
        return _parse_session_window(tuple(row)) if row else None

    def get_session_windows(
        self,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> list[SessionWindow]:
        """Return session windows ordered by start, optionally range-bounded."""
        clauses: list[str] = []
        params: list[str] = []
        if start is not None:
            clauses.append("started_at >= ?")
            params.append(start.isoformat())
        if end is not None:
            clauses.append("started_at < ?")
            params.append(end.isoformat())
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_SESSION_COLUMNS} FROM sessions{where} "
                "ORDER BY started_at",
                tuple(params),
            ).fetchall()
        return [_parse_session_window(tuple(r)) for r in rows]

    def update_session_window(self, window: SessionWindow) -> SessionWindow:
        """Update the mutable fields of an existing session window in place.

        Used by the incremental sessionizer when an ingest extends or shrinks a
        session's bounds without changing its identity (row id preserved).
        """
        if window.id is None:
            raise ValueError("update_session_window requires a persisted window id")
        with self._connect() as conn:
            conn.execute(
                "UPDATE sessions SET task_id = ?, repo = ?, started_at = ?, "
                "ended_at = ?, strategy_name = ?, category = ?, pr_num = ? "
                "WHERE id = ?",
                (
                    window.task_id,
                    window.repo,
                    window.started_at.isoformat(),
                    window.ended_at.isoformat(),
                    window.strategy_name,
                    window.category,
                    window.pr_num,
                    window.id,
                ),
            )
        return self.get_session_window(window.id)  # type: ignore[return-value]

    def delete_session_window(self, window_id: int) -> None:
        """Delete one session window; the FK nulls any lingering event links."""
        with self._connect() as conn:
            conn.execute("DELETE FROM sessions WHERE id = ?", (window_id,))

    def get_sessions_overlapping(
        self,
        start: datetime,
        end: datetime,
        *,
        task_id: Optional[str] = None,
        repo: Optional[str] = None,
        strategy_name: Optional[str] = None,
    ) -> list[SessionWindow]:
        """Return whole sessions that overlap ``[start, end]``.

        A session overlaps when ``started_at <= end AND ended_at >= start``. The
        session is returned whole (its true global boundaries), never clipped to
        the query range, so cross-day and cross-range sessions read identically
        regardless of the window they are queried through. Optional ``task_id``,
        ``repo``, and ``strategy_name`` filters narrow the result.
        """
        clauses = ["started_at <= ?", "ended_at >= ?"]
        params: list[str] = [end.isoformat(), start.isoformat()]
        for column, value in (
            ("task_id", task_id),
            ("repo", repo),
            ("strategy_name", strategy_name),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        where = " AND ".join(clauses)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_SESSION_COLUMNS} FROM sessions WHERE {where} "
                "ORDER BY started_at",
                tuple(params),
            ).fetchall()
        return [_parse_session_window(tuple(r)) for r in rows]

    def clear_session_windows(self) -> None:
        """Delete all computed session windows (a fresh materialization).

        The events.session_id FK (ON DELETE SET NULL) unlinks every event so no
        dangling link survives a full re-materialization.
        """
        with self._connect() as conn:
            conn.execute("DELETE FROM sessions")

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
