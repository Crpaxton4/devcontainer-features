"""Tests for odoo_sdk.cli.__main__ CLI companion."""

import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import odoo_sdk.cli.__main__ as cli
from odoo_sdk.task_tracker.state import TaskStateDB


ASSERT_GUARD = "odoo_sdk.cli.__main__.assert_odoo_devcontainer"


def _tmp_db() -> TaskStateDB:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return TaskStateDB(db_path=Path(tmp.name))


def _client() -> MagicMock:
    return MagicMock()


# ── _assert_env ───────────────────────────────────────────────────────────────

class TestAssertEnv(unittest.TestCase):
    def test_exits_when_not_devcontainer(self):
        from odoo_sdk.task_tracker.env_check import OdooDevcontainerRequiredError

        with patch(ASSERT_GUARD, side_effect=OdooDevcontainerRequiredError("bad env")):
            with self.assertRaises(SystemExit) as ctx:
                cli._assert_env()
        self.assertEqual(ctx.exception.code, 1)

    def test_passes_in_devcontainer(self):
        with patch(ASSERT_GUARD):
            cli._assert_env()  # must not raise


# ── cmd_list ─────────────────────────────────────────────────────────────────

class TestCmdList(unittest.TestCase):
    def test_prints_nothing_to_stop(self):
        db = _tmp_db()
        args = MagicMock()
        captured = StringIO()
        with patch("sys.stdout", captured):
            cli.cmd_list(db, args)
        self.assertIn("No active", captured.getvalue())

    def test_prints_active_sessions(self):
        db = _tmp_db()
        db.create_session(1, "Bug Fix", 10, "Accounting", timesheet_id=1)
        args = MagicMock()
        captured = StringIO()
        with patch("sys.stdout", captured):
            cli.cmd_list(db, args)
        self.assertIn("Bug Fix", captured.getvalue())


# ── cmd_stop ─────────────────────────────────────────────────────────────────

class TestCmdStop(unittest.TestCase):
    def test_stops_active_session(self):
        db = _tmp_db()
        db.create_session(1, "Bug", 10, "Project A", timesheet_id=50)
        session_id = db.get_active_session(1).id
        args = MagicMock(session_id=session_id)
        client = _client()
        captured = StringIO()
        with (
            patch("sys.stdout", captured),
            patch("odoo_sdk.cli.__main__.update_timesheet") as mock_update,
        ):
            cli.cmd_stop(db, args, client)
        mock_update.assert_called_once()
        self.assertIn("Stopped", captured.getvalue())

    def test_skips_already_stopped(self):
        db = _tmp_db()
        db.create_session(1, "Bug", 10, "Project A", timesheet_id=50)
        db.stop_session(1)
        session_id = db.get_session_by_id(1).id
        args = MagicMock(session_id=session_id)
        captured = StringIO()
        with patch("sys.stdout", captured):
            cli.cmd_stop(db, args, _client())
        self.assertIn("already stopped", captured.getvalue())

    def test_exits_for_unknown_session(self):
        db = _tmp_db()
        args = MagicMock(session_id=9999)
        with self.assertRaises(SystemExit):
            cli.cmd_stop(db, args, _client())

    def test_skips_timesheet_update_when_no_timesheet(self):
        db = _tmp_db()
        db.create_session(1, "Bug", 10, "Project A", timesheet_id=None)
        session_id = db.get_active_session(1).id
        args = MagicMock(session_id=session_id)
        with patch("odoo_sdk.cli.__main__.update_timesheet") as mock_update:
            cli.cmd_stop(db, args, _client())
        mock_update.assert_not_called()


# ── cmd_stop_all ──────────────────────────────────────────────────────────────

class TestCmdStopAll(unittest.TestCase):
    def test_nothing_to_stop(self):
        db = _tmp_db()
        args = MagicMock()
        captured = StringIO()
        with patch("sys.stdout", captured):
            cli.cmd_stop_all(db, args, _client())
        self.assertIn("Nothing", captured.getvalue())

    def test_stops_all_active(self):
        db = _tmp_db()
        db.create_session(1, "Bug", 10, "Project A", timesheet_id=1)
        db.create_session(2, "Feature", 10, "Project A", timesheet_id=2)
        args = MagicMock()
        captured = StringIO()
        with (
            patch("sys.stdout", captured),
            patch("odoo_sdk.cli.__main__.update_timesheet"),
        ):
            cli.cmd_stop_all(db, args, _client())
        self.assertIn("Stopped", captured.getvalue())
        self.assertEqual(len(db.get_all_active_sessions()), 0)


# ── cmd_report ────────────────────────────────────────────────────────────────

class TestCmdReport(unittest.TestCase):
    def test_active_only_by_default(self):
        db = _tmp_db()
        db.create_session(1, "Bug", 10, "Project A", timesheet_id=1)
        db.create_session(2, "Done", 10, "Project A", timesheet_id=2)
        db.stop_session(2)
        args = MagicMock(all=False)
        captured = StringIO()
        with patch("sys.stdout", captured):
            cli.cmd_report(db, args)
        output = captured.getvalue()
        self.assertIn("Bug", output)
        self.assertNotIn("Done", output)

    def test_all_flag_includes_stopped(self):
        db = _tmp_db()
        db.create_session(1, "Bug", 10, "Project A", timesheet_id=1)
        db.stop_session(1)
        args = MagicMock(all=True)
        captured = StringIO()
        with patch("sys.stdout", captured):
            cli.cmd_report(db, args)
        self.assertIn("Bug", captured.getvalue())

    def test_empty_report(self):
        db = _tmp_db()
        args = MagicMock(all=False)
        captured = StringIO()
        with patch("sys.stdout", captured):
            cli.cmd_report(db, args)
        self.assertIn("No sessions", captured.getvalue())


# ── cmd_normalize ─────────────────────────────────────────────────────────────

class TestCmdNormalize(unittest.TestCase):
    def test_no_duplicates_message(self):
        db = _tmp_db()
        args = MagicMock(apply=False)
        captured = StringIO()
        with patch("sys.stdout", captured):
            cli.cmd_normalize(db, args, _client())
        self.assertIn("No duplicate", captured.getvalue())

    def test_dry_run_reports_without_merging(self):
        db = _tmp_db()
        db.create_session(1, "Bug", 10, "Project A", timesheet_id=10)
        db.stop_session(1)
        db.create_session(1, "Bug", 10, "Project A", timesheet_id=11)
        db.stop_session(1)
        args = MagicMock(apply=False)
        captured = StringIO()
        with patch("sys.stdout", captured):
            cli.cmd_normalize(db, args, _client())
        output = captured.getvalue()
        self.assertIn("Dry run", output)
        self.assertIn("[10, 11]", output)

    def test_apply_merges(self):
        db = _tmp_db()
        db.create_session(1, "Bug", 10, "Project A", timesheet_id=10)
        db.stop_session(1)
        db.create_session(1, "Bug", 10, "Project A", timesheet_id=11)
        db.stop_session(1)
        args = MagicMock(apply=True)
        captured = StringIO()
        with (
            patch("sys.stdout", captured),
            patch("odoo_sdk.cli.__main__.merge_timesheets") as mock_merge,
        ):
            cli.cmd_normalize(db, args, _client())
        mock_merge.assert_called_once()
        self.assertIn("Merged", captured.getvalue())


# ── main entrypoint ───────────────────────────────────────────────────────────

class TestMain(unittest.TestCase):
    def _run_main(self, argv, db=None):
        if db is None:
            db = _tmp_db()
        with (
            patch(ASSERT_GUARD),
            patch("odoo_sdk.cli.__main__.TaskStateDB", return_value=db),
            patch("sys.argv", ["odoo_sdk.cli"] + argv),
        ):
            cli.main()

    def test_default_command_is_list(self):
        captured = StringIO()
        with patch("sys.stdout", captured):
            self._run_main([])
        self.assertIn("No active", captured.getvalue())

    def test_list_command(self):
        captured = StringIO()
        with patch("sys.stdout", captured):
            self._run_main(["list"])
        self.assertIn("No active", captured.getvalue())

    def test_stop_all_command(self):
        captured = StringIO()
        with (
            patch("sys.stdout", captured),
            patch("odoo_sdk.cli.__main__.OdooClient"),
        ):
            self._run_main(["stop-all"])
        self.assertIn("Nothing", captured.getvalue())

    def test_report_command(self):
        captured = StringIO()
        with patch("sys.stdout", captured):
            self._run_main(["report"])
        self.assertIn("No sessions", captured.getvalue())

    def test_normalize_command(self):
        captured = StringIO()
        with (
            patch("sys.stdout", captured),
            patch("odoo_sdk.cli.__main__.OdooClient"),
        ):
            self._run_main(["normalize"])
        self.assertIn("No duplicate", captured.getvalue())

    def test_stop_command(self):
        db = _tmp_db()
        db.create_session(1, "Bug", 10, "Project A", timesheet_id=1)
        session_id = db.get_active_session(1).id
        captured = StringIO()
        with (
            patch("sys.stdout", captured),
            patch("odoo_sdk.cli.__main__.OdooClient"),
            patch("odoo_sdk.cli.__main__.update_timesheet"),
            patch(ASSERT_GUARD),
            patch("odoo_sdk.cli.__main__.TaskStateDB", return_value=db),
            patch("sys.argv", ["odoo_sdk.cli", "stop", str(session_id)]),
        ):
            cli.main()
        self.assertIn("Stopped", captured.getvalue())


if __name__ == "__main__":
    unittest.main()
