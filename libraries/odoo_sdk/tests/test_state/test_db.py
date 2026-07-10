import sqlite3
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from odoo_sdk.state.db import (
    LocalStateClient as TaskStateDB,
    _get_project_dir,
)
from odoo_sdk.state.models import (
    InvalidStateTransitionError,
    ProjectIdError,
    TaskAlreadyRunningError,
    TaskNotRunningError,
    TaskRun,
    TaskState,
)


def _tmp_db() -> TaskStateDB:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return TaskStateDB(db_path=Path(tmp.name))


def _create(db: TaskStateDB, task_id: int = 1, timesheet_id: int = 100) -> TaskRun:
    return db.create_run(
        task_id=task_id,
        task_name=f"Task {task_id}",
        project_id=10,
        project_name="Project X",
        timesheet_id=timesheet_id,
    )


class TestTaskRunProperties(unittest.TestCase):
    def _run(self, started_at: datetime, stopped_at=None) -> TaskRun:
        return TaskRun(
            id=1,
            task_id=1,
            task_name="T",
            project_id=10,
            project_name="P",
            state=TaskState.RUNNING,
            started_at=started_at,
            stopped_at=stopped_at,
            timesheet_id=None,
            notes=[],
        )

    def test_elapsed_seconds_running_uses_now(self):
        started = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        fixed_now = datetime(2024, 1, 1, 13, 0, 0, tzinfo=timezone.utc)
        run = self._run(started)
        with patch("odoo_sdk.state.models.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_now
            mock_dt.fromisoformat = datetime.fromisoformat
            self.assertAlmostEqual(run.elapsed_seconds, 3600, delta=1)

    def test_elapsed_seconds_stopped_uses_stopped_at(self):
        started = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        stopped = datetime(2024, 1, 1, 12, 30, 0, tzinfo=timezone.utc)
        run = self._run(started, stopped_at=stopped)
        self.assertAlmostEqual(run.elapsed_seconds, 1800, delta=1)

    def test_elapsed_hours(self):
        started = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        stopped = datetime(2024, 1, 1, 11, 30, 0, tzinfo=timezone.utc)
        run = self._run(started, stopped_at=stopped)
        self.assertAlmostEqual(run.elapsed_hours, 1.5, places=4)

    def test_elapsed_human_format(self):
        started = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        stopped = datetime(2024, 1, 1, 11, 2, 5, tzinfo=timezone.utc)
        run = self._run(started, stopped_at=stopped)
        self.assertEqual(run.elapsed_human, "1h 2m 5s")


class TestTaskStateDBCreate(unittest.TestCase):
    def test_create_returns_running_run(self):
        db = _tmp_db()
        r = _create(db)
        self.assertEqual(r.state, TaskState.RUNNING)
        self.assertEqual(r.task_id, 1)
        self.assertEqual(r.task_name, "Task 1")
        self.assertEqual(r.project_id, 10)
        self.assertEqual(r.timesheet_id, 100)
        self.assertIsNone(r.stopped_at)
        self.assertEqual(r.notes, [])

    def test_create_raises_when_active_run_exists(self):
        db = _tmp_db()
        _create(db, task_id=1)
        with self.assertRaises(TaskAlreadyRunningError):
            _create(db, task_id=1)

    def test_create_allows_parallel_tasks(self):
        db = _tmp_db()
        r1 = _create(db, task_id=1)
        r2 = _create(db, task_id=2)
        self.assertEqual(r1.state, TaskState.RUNNING)
        self.assertEqual(r2.state, TaskState.RUNNING)

    def test_create_allows_new_run_after_stop(self):
        db = _tmp_db()
        _create(db, task_id=1)
        db.stop_run(1)
        r = _create(db, task_id=1)
        self.assertEqual(r.state, TaskState.RUNNING)


class TestTaskStateDBGetters(unittest.TestCase):
    def test_get_active_run_returns_running(self):
        db = _tmp_db()
        _create(db, task_id=5)
        r = db.get_active_run(5)
        self.assertIsNotNone(r)
        self.assertEqual(r.task_id, 5)  # type: ignore[union-attr]

    def test_get_active_run_returns_none_when_stopped(self):
        db = _tmp_db()
        _create(db, task_id=5)
        db.stop_run(5)
        self.assertIsNone(db.get_active_run(5))

    def test_get_active_run_returns_none_for_unknown(self):
        db = _tmp_db()
        self.assertIsNone(db.get_active_run(999))

    def test_get_active_run_returns_awaiting_answers(self):
        db = _tmp_db()
        _create(db, task_id=3)
        db.transition_to_awaiting(3)
        r = db.get_active_run(3)
        self.assertEqual(r.state, TaskState.AWAITING_ANSWERS)  # type: ignore[union-attr]

    def test_get_all_active_runs_multiple(self):
        db = _tmp_db()
        _create(db, task_id=1)
        _create(db, task_id=2)
        _create(db, task_id=3)
        db.stop_run(3)
        runs = db.get_all_active_runs()
        task_ids = {r.task_id for r in runs}
        self.assertEqual(task_ids, {1, 2})

    def test_get_all_active_runs_empty(self):
        db = _tmp_db()
        self.assertEqual(db.get_all_active_runs(), [])

    def test_get_all_runs_includes_stopped(self):
        db = _tmp_db()
        _create(db, task_id=1)
        db.stop_run(1)
        _create(db, task_id=2)
        runs = db.get_all_runs()
        self.assertEqual(len(runs), 2)

    def test_get_stopped_runs_with_timesheet(self):
        db = _tmp_db()
        _create(db, task_id=1, timesheet_id=50)
        db.stop_run(1)
        _create(db, task_id=2, timesheet_id=None)
        db.stop_run(2)
        runs = db.get_stopped_runs_with_timesheet()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].task_id, 1)

    def test_get_run_by_id_returns_correct(self):
        db = _tmp_db()
        created = _create(db)
        fetched = db.get_run_by_id(created.id)
        self.assertEqual(fetched.id, created.id)  # type: ignore[union-attr]

    def test_get_run_by_id_returns_none_for_unknown(self):
        db = _tmp_db()
        self.assertIsNone(db.get_run_by_id(9999))


class TestTaskStateDBTransitions(unittest.TestCase):
    def test_transition_to_awaiting_from_running(self):
        db = _tmp_db()
        _create(db, task_id=1)
        r = db.transition_to_awaiting(1)
        self.assertEqual(r.state, TaskState.AWAITING_ANSWERS)

    def test_transition_to_awaiting_self_loop(self):
        db = _tmp_db()
        _create(db, task_id=1)
        db.transition_to_awaiting(1)
        r = db.transition_to_awaiting(1)
        self.assertEqual(r.state, TaskState.AWAITING_ANSWERS)

    def test_transition_to_awaiting_no_run_raises(self):
        db = _tmp_db()
        with self.assertRaises(TaskNotRunningError):
            db.transition_to_awaiting(999)

    def test_transition_to_running_from_awaiting(self):
        db = _tmp_db()
        _create(db, task_id=1)
        db.transition_to_awaiting(1)
        r = db.transition_to_running(1)
        self.assertEqual(r.state, TaskState.RUNNING)

    def test_transition_to_running_from_running_raises(self):
        db = _tmp_db()
        _create(db, task_id=1)
        with self.assertRaises(InvalidStateTransitionError):
            db.transition_to_running(1)

    def test_transition_to_running_no_run_raises(self):
        db = _tmp_db()
        with self.assertRaises(TaskNotRunningError):
            db.transition_to_running(999)

    def test_stop_running_run(self):
        db = _tmp_db()
        _create(db, task_id=1)
        r = db.stop_run(1)
        self.assertEqual(r.state, TaskState.STOPPED)
        self.assertIsNotNone(r.stopped_at)

    def test_stop_awaiting_answers_run(self):
        db = _tmp_db()
        _create(db, task_id=1)
        db.transition_to_awaiting(1)
        r = db.stop_run(1)
        self.assertEqual(r.state, TaskState.STOPPED)

    def test_stop_no_run_raises(self):
        db = _tmp_db()
        with self.assertRaises(TaskNotRunningError):
            db.stop_run(999)

    def test_stop_with_timesheet_override(self):
        db = _tmp_db()
        _create(db, task_id=1, timesheet_id=10)
        r = db.stop_run(1, timesheet_id=99)
        self.assertEqual(r.timesheet_id, 99)


class TestTaskStateDBNotes(unittest.TestCase):
    def test_append_note_adds_to_run(self):
        db = _tmp_db()
        _create(db, task_id=1)
        db.append_note(1, "First note")
        db.append_note(1, "Second note")
        r = db.get_active_run(1)
        self.assertEqual(r.notes, ["First note", "Second note"])  # type: ignore[union-attr]

    def test_append_note_no_run_raises(self):
        db = _tmp_db()
        with self.assertRaises(TaskNotRunningError):
            db.append_note(999, "note")


class TestTaskStateDBSettings(unittest.TestCase):
    def test_set_and_get_setting(self):
        db = _tmp_db()
        db.set_setting("employee_id", "42")
        self.assertEqual(db.get_setting("employee_id"), "42")

    def test_get_setting_returns_none_for_missing(self):
        db = _tmp_db()
        self.assertIsNone(db.get_setting("nonexistent"))

    def test_set_setting_upserts(self):
        db = _tmp_db()
        db.set_setting("k", "v1")
        db.set_setting("k", "v2")
        self.assertEqual(db.get_setting("k"), "v2")


class TestTaskStateDBOtherOps(unittest.TestCase):
    def test_update_timesheet_id(self):
        db = _tmp_db()
        r = _create(db, task_id=1, timesheet_id=10)
        db.update_timesheet_id(r.id, 99)
        updated = db.get_run_by_id(r.id)
        self.assertEqual(updated.timesheet_id, 99)  # type: ignore[union-attr]

    def test_remap_timesheet_id(self):
        db = _tmp_db()
        _create(db, task_id=1, timesheet_id=10)
        db.stop_run(1)
        db.remap_timesheet_id(10, 20)
        r = db.get_run_by_id(1)
        self.assertEqual(r.timesheet_id, 20)  # type: ignore[union-attr]


class TestTaskSessionsMigration(unittest.TestCase):
    def test_legacy_task_sessions_rows_preserved_under_task_runs(self):
        """A pre-rename DB with a populated ``task_sessions`` table has its rows
        preserved under ``task_runs`` when opened by ``LocalStateClient``."""
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        db_path = Path(tmp.name)

        # Build a legacy database with the old table name and one row.
        started_at = datetime(2024, 1, 1, 9, 0, 0, tzinfo=timezone.utc).isoformat()
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """
            CREATE TABLE task_sessions (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id      INTEGER NOT NULL,
                task_name    TEXT    NOT NULL,
                project_id   INTEGER NOT NULL,
                project_name TEXT    NOT NULL,
                state        TEXT    NOT NULL,
                started_at   TEXT    NOT NULL,
                stopped_at   TEXT,
                timesheet_id INTEGER,
                notes        TEXT    NOT NULL DEFAULT '[]'
            )
            """
        )
        conn.execute(
            "INSERT INTO task_sessions (task_id, task_name, project_id, "
            "project_name, state, started_at, timesheet_id, notes) "
            "VALUES (?, ?, ?, ?, 'RUNNING', ?, ?, '[]')",
            (7, "Legacy Task", 3, "Legacy Project", started_at, 42),
        )
        conn.commit()
        conn.close()

        # Opening the store runs the migration.
        db = TaskStateDB(db_path=db_path)

        # The old table is gone and the row survives under task_runs.
        with sqlite3.connect(str(db_path)) as check:
            tables = {
                row[0]
                for row in check.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        self.assertIn("task_runs", tables)
        self.assertNotIn("task_sessions", tables)

        run = db.get_active_run(7)
        self.assertIsNotNone(run)
        self.assertEqual(run.task_name, "Legacy Task")  # type: ignore[union-attr]
        self.assertEqual(run.project_name, "Legacy Project")  # type: ignore[union-attr]
        self.assertEqual(run.timesheet_id, 42)  # type: ignore[union-attr]

    def test_migration_noop_leaves_new_db_with_task_runs(self):
        """A fresh database creates ``task_runs`` directly (no legacy table)."""
        db = _tmp_db()
        with sqlite3.connect(str(db._db_path)) as check:
            tables = {
                row[0]
                for row in check.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        self.assertIn("task_runs", tables)
        self.assertNotIn("task_sessions", tables)


class TestGetProjectDir(unittest.TestCase):
    def test_raises_project_id_error_on_git_failure(self):
        with patch(
            "odoo_sdk.state.db.subprocess.run",
            side_effect=subprocess.CalledProcessError(128, "git"),
        ):
            with self.assertRaises(ProjectIdError):
                _get_project_dir()

    def test_creates_dir_from_remote_url(self):
        mock_run = type("R", (), {"stdout": "git@github.com:org/repo.git\n"})()
        with (
            patch("odoo_sdk.state.db.subprocess.run", return_value=mock_run),
            patch.dict("os.environ", {"ODOO_TASK_TRACKER_DIR": "/tmp/tt-test"}),
            patch("odoo_sdk.state.db.Path.mkdir"),
        ):
            path1 = _get_project_dir()
            path2 = _get_project_dir()
        self.assertEqual(len(path1.name), 16)
        self.assertEqual(path1, path2)


if __name__ == "__main__":
    unittest.main()
