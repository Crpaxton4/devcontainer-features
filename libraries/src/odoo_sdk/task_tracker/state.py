"""SQLite-backed FSM state for task time-tracking sessions."""

import hashlib
import json
import os
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

_DEFAULT_ROOT = Path("/usr/local/share/odoo-task-tracker")


class TaskState(str, Enum):
    RUNNING = "RUNNING"
    AWAITING_ANSWERS = "AWAITING_ANSWERS"
    STOPPED = "STOPPED"


class ProjectIdError(RuntimeError):
    """Raised when the git remote origin URL cannot be determined."""


class TaskAlreadyRunningError(RuntimeError):
    """Raised when start_task is called for a task that already has an active session."""


class TaskNotRunningError(RuntimeError):
    """Raised when an operation requires an active session but none exists."""


class InvalidStateTransitionError(RuntimeError):
    """Raised when a state transition is not permitted by the FSM."""


@dataclass
class TaskSession:
    id: int
    task_id: int
    task_name: str
    project_id: int
    project_name: str
    state: TaskState
    started_at: datetime
    stopped_at: Optional[datetime]
    timesheet_id: Optional[int]
    notes: list[str]

    @property
    def elapsed_seconds(self) -> float:
        end = self.stopped_at or datetime.now(timezone.utc)
        return (end - self.started_at).total_seconds()

    @property
    def elapsed_hours(self) -> float:
        return self.elapsed_seconds / 3600

    @property
    def elapsed_human(self) -> str:
        total = int(self.elapsed_seconds)
        h, rem = divmod(total, 3600)
        m, s = divmod(rem, 60)
        return f"{h}h {m}m {s}s"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_sessions (
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


def _get_project_dir() -> Path:
    root = Path(os.environ.get("ODOO_TASK_TRACKER_DIR", _DEFAULT_ROOT))
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
    return project_dir


def _parse_session(row: tuple) -> TaskSession:
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
    return TaskSession(
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


class TaskStateDB:
    """SQLite-backed state store for task tracking sessions."""

    def __init__(self, db_path: Optional[Path] = None):
        if db_path is None:
            db_path = _get_project_dir() / "tasks.db"
        self._db_path = db_path
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def get_active_session(self, task_id: int) -> Optional[TaskSession]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, task_id, task_name, project_id, project_name, state, "
                "started_at, stopped_at, timesheet_id, notes "
                "FROM task_sessions WHERE task_id = ? AND state IN ('RUNNING', 'AWAITING_ANSWERS')",
                (task_id,),
            ).fetchone()
        return _parse_session(tuple(row)) if row else None

    def get_session_by_id(self, session_id: int) -> Optional[TaskSession]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, task_id, task_name, project_id, project_name, state, "
                "started_at, stopped_at, timesheet_id, notes "
                "FROM task_sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        return _parse_session(tuple(row)) if row else None

    def get_all_active_sessions(self) -> list[TaskSession]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, task_id, task_name, project_id, project_name, state, "
                "started_at, stopped_at, timesheet_id, notes "
                "FROM task_sessions WHERE state IN ('RUNNING', 'AWAITING_ANSWERS') "
                "ORDER BY started_at"
            ).fetchall()
        return [_parse_session(tuple(r)) for r in rows]

    def get_all_sessions(self) -> list[TaskSession]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, task_id, task_name, project_id, project_name, state, "
                "started_at, stopped_at, timesheet_id, notes "
                "FROM task_sessions ORDER BY started_at"
            ).fetchall()
        return [_parse_session(tuple(r)) for r in rows]

    def get_stopped_sessions_with_timesheet(self) -> list[TaskSession]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, task_id, task_name, project_id, project_name, state, "
                "started_at, stopped_at, timesheet_id, notes "
                "FROM task_sessions WHERE state = 'STOPPED' AND timesheet_id IS NOT NULL "
                "ORDER BY started_at"
            ).fetchall()
        return [_parse_session(tuple(r)) for r in rows]

    def create_session(
        self,
        task_id: int,
        task_name: str,
        project_id: int,
        project_name: str,
        timesheet_id: Optional[int] = None,
    ) -> TaskSession:
        existing = self.get_active_session(task_id)
        if existing is not None:
            raise TaskAlreadyRunningError(
                f"Task {task_id!r} ({task_name!r}) already has an active session "
                f"(id={existing.id}, state={existing.state.value})."
            )
        started_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO task_sessions (task_id, task_name, project_id, project_name, "
                "state, started_at, timesheet_id, notes) VALUES (?, ?, ?, ?, 'RUNNING', ?, ?, '[]')",
                (task_id, task_name, project_id, project_name, started_at, timesheet_id),
            )
            session_id = cursor.lastrowid
        return self.get_session_by_id(session_id)  # type: ignore[return-value]

    def transition_to_awaiting(self, task_id: int) -> TaskSession:
        session = self.get_active_session(task_id)
        if session is None:
            raise TaskNotRunningError(
                f"No active session found for task {task_id}."
            )
        if session.state not in (TaskState.RUNNING, TaskState.AWAITING_ANSWERS):
            raise InvalidStateTransitionError(
                f"Cannot transition task {task_id} to AWAITING_ANSWERS from {session.state.value}."
            )
        with self._connect() as conn:
            conn.execute(
                "UPDATE task_sessions SET state = 'AWAITING_ANSWERS' WHERE id = ?",
                (session.id,),
            )
        return self.get_session_by_id(session.id)  # type: ignore[return-value]

    def transition_to_running(self, task_id: int) -> TaskSession:
        session = self.get_active_session(task_id)
        if session is None:
            raise TaskNotRunningError(
                f"No active session found for task {task_id}."
            )
        if session.state != TaskState.AWAITING_ANSWERS:
            raise InvalidStateTransitionError(
                f"Cannot resume task {task_id}: expected AWAITING_ANSWERS, "
                f"got {session.state.value}."
            )
        with self._connect() as conn:
            conn.execute(
                "UPDATE task_sessions SET state = 'RUNNING' WHERE id = ?",
                (session.id,),
            )
        return self.get_session_by_id(session.id)  # type: ignore[return-value]

    def stop_session(self, task_id: int, timesheet_id: Optional[int] = None) -> TaskSession:
        session = self.get_active_session(task_id)
        if session is None:
            raise TaskNotRunningError(
                f"No active session found for task {task_id}."
            )
        stopped_at = datetime.now(timezone.utc).isoformat()
        tid = timesheet_id if timesheet_id is not None else session.timesheet_id
        with self._connect() as conn:
            conn.execute(
                "UPDATE task_sessions SET state = 'STOPPED', stopped_at = ?, timesheet_id = ? "
                "WHERE id = ?",
                (stopped_at, tid, session.id),
            )
        return self.get_session_by_id(session.id)  # type: ignore[return-value]

    def update_timesheet_id(self, session_id: int, timesheet_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE task_sessions SET timesheet_id = ? WHERE id = ?",
                (timesheet_id, session_id),
            )

    def append_note(self, task_id: int, note: str) -> None:
        session = self.get_active_session(task_id)
        if session is None:
            raise TaskNotRunningError(f"No active session for task {task_id}.")
        updated_notes = json.dumps(session.notes + [note])
        with self._connect() as conn:
            conn.execute(
                "UPDATE task_sessions SET notes = ? WHERE id = ?",
                (updated_notes, session.id),
            )

    def remap_timesheet_id(self, old_timesheet_id: int, new_timesheet_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE task_sessions SET timesheet_id = ? WHERE timesheet_id = ?",
                (new_timesheet_id, old_timesheet_id),
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
