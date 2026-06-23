import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from odoo_sdk.task_tracker.state import (
    InvalidStateTransitionError,
    ProjectIdError,
    TaskAlreadyRunningError,
    TaskNotRunningError,
    TaskSession,
    TaskState,
    TaskStateDB,
    _get_project_dir,
)


def _tmp_db() -> TaskStateDB:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return TaskStateDB(db_path=Path(tmp.name))


def _create(db: TaskStateDB, task_id: int = 1, timesheet_id: int = 100) -> TaskSession:
    return db.create_session(
        task_id=task_id,
        task_name=f"Task {task_id}",
        project_id=10,
        project_name="Project X",
        timesheet_id=timesheet_id,
    )


class TestTaskSessionProperties(unittest.TestCase):
    def _session(self, started_at: datetime, stopped_at=None) -> TaskSession:
        return TaskSession(
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
        session = self._session(started)
        with patch("odoo_sdk.task_tracker.state.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_now
            mock_dt.fromisoformat = datetime.fromisoformat
            self.assertAlmostEqual(session.elapsed_seconds, 3600, delta=1)

    def test_elapsed_seconds_stopped_uses_stopped_at(self):
        started = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        stopped = datetime(2024, 1, 1, 12, 30, 0, tzinfo=timezone.utc)
        session = self._session(started, stopped_at=stopped)
        self.assertAlmostEqual(session.elapsed_seconds, 1800, delta=1)

    def test_elapsed_hours(self):
        started = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        stopped = datetime(2024, 1, 1, 11, 30, 0, tzinfo=timezone.utc)
        session = self._session(started, stopped_at=stopped)
        self.assertAlmostEqual(session.elapsed_hours, 1.5, places=4)

    def test_elapsed_human_format(self):
        started = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        stopped = datetime(2024, 1, 1, 11, 2, 5, tzinfo=timezone.utc)
        session = self._session(started, stopped_at=stopped)
        self.assertEqual(session.elapsed_human, "1h 2m 5s")


class TestTaskStateDBCreate(unittest.TestCase):
    def test_create_returns_running_session(self):
        db = _tmp_db()
        s = _create(db)
        self.assertEqual(s.state, TaskState.RUNNING)
        self.assertEqual(s.task_id, 1)
        self.assertEqual(s.task_name, "Task 1")
        self.assertEqual(s.project_id, 10)
        self.assertEqual(s.timesheet_id, 100)
        self.assertIsNone(s.stopped_at)
        self.assertEqual(s.notes, [])

    def test_create_raises_when_active_session_exists(self):
        db = _tmp_db()
        _create(db, task_id=1)
        with self.assertRaises(TaskAlreadyRunningError):
            _create(db, task_id=1)

    def test_create_allows_parallel_tasks(self):
        db = _tmp_db()
        s1 = _create(db, task_id=1)
        s2 = _create(db, task_id=2)
        self.assertEqual(s1.state, TaskState.RUNNING)
        self.assertEqual(s2.state, TaskState.RUNNING)

    def test_create_allows_new_session_after_stop(self):
        db = _tmp_db()
        _create(db, task_id=1)
        db.stop_session(1)
        s = _create(db, task_id=1)
        self.assertEqual(s.state, TaskState.RUNNING)


class TestTaskStateDBGetters(unittest.TestCase):
    def test_get_active_session_returns_running(self):
        db = _tmp_db()
        _create(db, task_id=5)
        s = db.get_active_session(5)
        self.assertIsNotNone(s)
        self.assertEqual(s.task_id, 5)  # type: ignore[union-attr]

    def test_get_active_session_returns_none_when_stopped(self):
        db = _tmp_db()
        _create(db, task_id=5)
        db.stop_session(5)
        self.assertIsNone(db.get_active_session(5))

    def test_get_active_session_returns_none_for_unknown(self):
        db = _tmp_db()
        self.assertIsNone(db.get_active_session(999))

    def test_get_active_session_returns_awaiting_answers(self):
        db = _tmp_db()
        _create(db, task_id=3)
        db.transition_to_awaiting(3)
        s = db.get_active_session(3)
        self.assertEqual(s.state, TaskState.AWAITING_ANSWERS)  # type: ignore[union-attr]

    def test_get_all_active_sessions_multiple(self):
        db = _tmp_db()
        _create(db, task_id=1)
        _create(db, task_id=2)
        _create(db, task_id=3)
        db.stop_session(3)
        sessions = db.get_all_active_sessions()
        task_ids = {s.task_id for s in sessions}
        self.assertEqual(task_ids, {1, 2})

    def test_get_all_active_sessions_empty(self):
        db = _tmp_db()
        self.assertEqual(db.get_all_active_sessions(), [])

    def test_get_all_sessions_includes_stopped(self):
        db = _tmp_db()
        _create(db, task_id=1)
        db.stop_session(1)
        _create(db, task_id=2)
        sessions = db.get_all_sessions()
        self.assertEqual(len(sessions), 2)

    def test_get_stopped_sessions_with_timesheet(self):
        db = _tmp_db()
        _create(db, task_id=1, timesheet_id=50)
        db.stop_session(1)
        _create(db, task_id=2, timesheet_id=None)
        db.stop_session(2)
        sessions = db.get_stopped_sessions_with_timesheet()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].task_id, 1)

    def test_get_session_by_id_returns_correct(self):
        db = _tmp_db()
        created = _create(db)
        fetched = db.get_session_by_id(created.id)
        self.assertEqual(fetched.id, created.id)  # type: ignore[union-attr]

    def test_get_session_by_id_returns_none_for_unknown(self):
        db = _tmp_db()
        self.assertIsNone(db.get_session_by_id(9999))


class TestTaskStateDBTransitions(unittest.TestCase):
    def test_transition_to_awaiting_from_running(self):
        db = _tmp_db()
        _create(db, task_id=1)
        s = db.transition_to_awaiting(1)
        self.assertEqual(s.state, TaskState.AWAITING_ANSWERS)

    def test_transition_to_awaiting_self_loop(self):
        db = _tmp_db()
        _create(db, task_id=1)
        db.transition_to_awaiting(1)
        s = db.transition_to_awaiting(1)
        self.assertEqual(s.state, TaskState.AWAITING_ANSWERS)

    def test_transition_to_awaiting_no_session_raises(self):
        db = _tmp_db()
        with self.assertRaises(TaskNotRunningError):
            db.transition_to_awaiting(999)

    def test_transition_to_running_from_awaiting(self):
        db = _tmp_db()
        _create(db, task_id=1)
        db.transition_to_awaiting(1)
        s = db.transition_to_running(1)
        self.assertEqual(s.state, TaskState.RUNNING)

    def test_transition_to_running_from_running_raises(self):
        db = _tmp_db()
        _create(db, task_id=1)
        with self.assertRaises(InvalidStateTransitionError):
            db.transition_to_running(1)

    def test_transition_to_running_no_session_raises(self):
        db = _tmp_db()
        with self.assertRaises(TaskNotRunningError):
            db.transition_to_running(999)

    def test_stop_running_session(self):
        db = _tmp_db()
        _create(db, task_id=1)
        s = db.stop_session(1)
        self.assertEqual(s.state, TaskState.STOPPED)
        self.assertIsNotNone(s.stopped_at)

    def test_stop_awaiting_answers_session(self):
        db = _tmp_db()
        _create(db, task_id=1)
        db.transition_to_awaiting(1)
        s = db.stop_session(1)
        self.assertEqual(s.state, TaskState.STOPPED)

    def test_stop_no_session_raises(self):
        db = _tmp_db()
        with self.assertRaises(TaskNotRunningError):
            db.stop_session(999)

    def test_stop_with_timesheet_override(self):
        db = _tmp_db()
        _create(db, task_id=1, timesheet_id=10)
        s = db.stop_session(1, timesheet_id=99)
        self.assertEqual(s.timesheet_id, 99)


class TestTaskStateDBNotes(unittest.TestCase):
    def test_append_note_adds_to_session(self):
        db = _tmp_db()
        _create(db, task_id=1)
        db.append_note(1, "First note")
        db.append_note(1, "Second note")
        s = db.get_active_session(1)
        self.assertEqual(s.notes, ["First note", "Second note"])  # type: ignore[union-attr]

    def test_append_note_no_session_raises(self):
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
        s = _create(db, task_id=1, timesheet_id=10)
        db.update_timesheet_id(s.id, 99)
        updated = db.get_session_by_id(s.id)
        self.assertEqual(updated.timesheet_id, 99)  # type: ignore[union-attr]

    def test_remap_timesheet_id(self):
        db = _tmp_db()
        _create(db, task_id=1, timesheet_id=10)
        db.stop_session(1)
        db.remap_timesheet_id(10, 20)
        s = db.get_session_by_id(1)
        self.assertEqual(s.timesheet_id, 20)  # type: ignore[union-attr]


class TestGetProjectDir(unittest.TestCase):
    def test_raises_project_id_error_on_git_failure(self):
        with patch(
            "odoo_sdk.task_tracker.state.subprocess.run",
            side_effect=subprocess.CalledProcessError(128, "git"),
        ):
            with self.assertRaises(ProjectIdError):
                _get_project_dir()

    def test_creates_dir_from_remote_url(self):
        with (
            patch(
                "odoo_sdk.task_tracker.state.subprocess.run",
                return_value=type("R", (), {"stdout": "git@github.com:org/repo.git\n"})(),
            ),
            patch.dict("os.environ", {"ODOO_TASK_TRACKER_DIR": "/tmp/tt-test"}),
            patch("odoo_sdk.task_tracker.state.Path.mkdir"),
        ):
            path = _get_project_dir()
        # Should be a subdir with a 16-char hex name
        self.assertEqual(len(path.name), 16)


if __name__ == "__main__":
    unittest.main()
