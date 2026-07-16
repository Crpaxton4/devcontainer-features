"""Tests for odoo_sdk.cli.__main__ CLI companion."""

import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import odoo_sdk.cli.__main__ as cli
from odoo_sdk.state import LocalStateClient as TaskStateDB
from odoo_sdk.state import TaskState
from tests.support import make_state_db


ASSERT_GUARD = "odoo_sdk.cli.__main__.assert_odoo_devcontainer"
STOP_GUARD = "odoo_sdk.commands.builtin.stop_task.assert_odoo_devcontainer"


def _tmp_db() -> TaskStateDB:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return make_state_db(Path(tmp.name))


def _client() -> MagicMock:
    return MagicMock()


def _registry(db: TaskStateDB, client: MagicMock = None):
    """Build the shared built-in registry over ``db`` for direct cmd_* calls."""
    from odoo_sdk.commands import Registry
    from odoo_sdk.commands.builtin import register_builtins

    return register_builtins(
        Registry(client if client is not None else MagicMock(), state_client=db)
    )


NORMALIZE_MERGE = "odoo_sdk.commands.builtin.normalize_timesheets.merge_timesheets"


# ── _assert_env ───────────────────────────────────────────────────────────────

class TestAssertEnv(unittest.TestCase):
    def test_exits_when_not_devcontainer(self):
        from odoo_sdk.utilities.env import OdooDevcontainerRequiredError

        with patch(ASSERT_GUARD, side_effect=OdooDevcontainerRequiredError("bad env")):
            with self.assertRaises(SystemExit) as ctx:
                cli._assert_env()
        self.assertEqual(ctx.exception.code, 1)

    def test_passes_in_devcontainer(self):
        with patch(ASSERT_GUARD):
            cli._assert_env()  # must not raise


# ── _LazyOdooClient ───────────────────────────────────────────────────────────

class TestLazyOdooClient(unittest.TestCase):
    def test_defers_construction_until_first_use(self):
        with patch("odoo_sdk.cli.__main__.OdooClient") as make_client:
            lazy = cli._LazyOdooClient()
            make_client.assert_not_called()  # construction is deferred
            real = make_client.return_value
            real.uid = 7
            real.__getitem__.return_value = "recordset"
            real.execute.return_value = "ok"

            self.assertEqual(lazy.uid, 7)
            self.assertEqual(lazy.execute("m", "read", 1), "ok")
            self.assertEqual(lazy["m"], "recordset")
        # Built exactly once and cached across every member access.
        make_client.assert_called_once_with()
        real.execute.assert_called_once_with("m", "read", 1)


# ── cmd_list ─────────────────────────────────────────────────────────────────

class TestCmdList(unittest.TestCase):
    def test_prints_nothing_to_stop(self):
        db = _tmp_db()
        args = MagicMock()
        captured = StringIO()
        with patch("sys.stdout", captured):
            cli.cmd_list(_registry(db), args)
        self.assertIn("No active", captured.getvalue())

    def test_prints_active_runs(self):
        db = _tmp_db()
        db.create_run(1, "Bug Fix", 10, "Accounting", timesheet_id=1)
        args = MagicMock()
        captured = StringIO()
        with patch("sys.stdout", captured):
            cli.cmd_list(_registry(db), args)
        self.assertIn("Bug Fix", captured.getvalue())


# ── cmd_stop ─────────────────────────────────────────────────────────────────

class TestCmdStop(unittest.TestCase):
    def test_stops_active_run(self):
        db = _tmp_db()
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=50)
        run_id = db.get_active_run(1).id
        args = MagicMock(run_id=run_id)
        captured = StringIO()
        with patch("sys.stdout", captured):
            cli.cmd_stop(_registry(db), args)
        self.assertIn("Stopped", captured.getvalue())
        self.assertEqual(db.get_run_by_id(run_id).state, TaskState.STOPPED)

    def test_stop_writes_no_timesheet_hours(self):
        # Regression (#402/#403): the CLI stop path routes through the command
        # layer (StopRunCommand), which never writes account.analytic.line hours
        # — the upload path owns unit_amount. The run carries a timesheet id, yet
        # no Odoo write may be attempted.
        db = _tmp_db()
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=50)
        run_id = db.get_active_run(1).id
        args = MagicMock(run_id=run_id)
        client = _client()
        with patch("sys.stdout", StringIO()):
            cli.cmd_stop(_registry(db, client), args)
        client.execute.assert_not_called()

    def test_skips_already_stopped(self):
        db = _tmp_db()
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=50)
        db.stop_run(1)
        run_id = db.get_run_by_id(1).id
        args = MagicMock(run_id=run_id)
        client = _client()
        captured = StringIO()
        with patch("sys.stdout", captured):
            cli.cmd_stop(_registry(db, client), args)
        self.assertIn("already stopped", captured.getvalue())
        client.execute.assert_not_called()

    def test_exits_for_unknown_run(self):
        db = _tmp_db()
        args = MagicMock(run_id=9999)
        with self.assertRaises(SystemExit):
            cli.cmd_stop(_registry(db), args)


# ── cmd_stop_all ──────────────────────────────────────────────────────────────

class TestCmdStopAll(unittest.TestCase):
    def test_nothing_to_stop(self):
        db = _tmp_db()
        args = MagicMock()
        captured = StringIO()
        with patch("sys.stdout", captured):
            cli.cmd_stop_all(_registry(db), args)
        self.assertIn("Nothing", captured.getvalue())

    def test_stops_all_active(self):
        db = _tmp_db()
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=1)
        db.create_run(2, "Feature", 10, "Project A", timesheet_id=2)
        args = MagicMock()
        captured = StringIO()
        with patch("sys.stdout", captured):
            cli.cmd_stop_all(_registry(db), args)
        self.assertIn("Stopped", captured.getvalue())
        self.assertEqual(len(db.get_all_active_runs()), 0)

    def test_stop_all_writes_no_timesheet_hours(self):
        # Regression (#402/#403): stop-all bills no hours at stop time; the
        # upload path owns account.analytic.line unit_amount for every surface.
        db = _tmp_db()
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=1)
        db.create_run(2, "Feature", 10, "Project A", timesheet_id=2)
        args = MagicMock()
        client = _client()
        with patch("sys.stdout", StringIO()):
            cli.cmd_stop_all(_registry(db, client), args)
        client.execute.assert_not_called()


# ── cmd_report ────────────────────────────────────────────────────────────────

class TestCmdReport(unittest.TestCase):
    def test_active_only_by_default(self):
        db = _tmp_db()
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=1)
        db.create_run(2, "Done", 10, "Project A", timesheet_id=2)
        db.stop_run(2)
        args = MagicMock(all=False)
        captured = StringIO()
        with patch("sys.stdout", captured):
            cli.cmd_report(_registry(db), args)
        output = captured.getvalue()
        self.assertIn("Bug", output)
        self.assertNotIn("Done", output)

    def test_all_flag_includes_stopped(self):
        db = _tmp_db()
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=1)
        db.stop_run(1)
        args = MagicMock(all=True)
        captured = StringIO()
        with patch("sys.stdout", captured):
            cli.cmd_report(_registry(db), args)
        self.assertIn("Bug", captured.getvalue())

    def test_empty_report(self):
        db = _tmp_db()
        args = MagicMock(all=False)
        captured = StringIO()
        with patch("sys.stdout", captured):
            cli.cmd_report(_registry(db), args)
        self.assertIn("No runs", captured.getvalue())


# ── cmd_normalize ─────────────────────────────────────────────────────────────

class TestCmdNormalize(unittest.TestCase):
    def test_no_duplicates_message(self):
        db = _tmp_db()
        args = MagicMock(apply=False)
        captured = StringIO()
        with patch("sys.stdout", captured):
            cli.cmd_normalize(_registry(db, _client()), args)
        self.assertIn("No duplicate", captured.getvalue())

    def test_dry_run_reports_without_merging(self):
        db = _tmp_db()
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=10)
        db.stop_run(1)
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=11)
        db.stop_run(1)
        args = MagicMock(apply=False)
        captured = StringIO()
        with (
            patch("sys.stdout", captured),
            patch(NORMALIZE_MERGE) as mock_merge,
        ):
            cli.cmd_normalize(_registry(db, _client()), args)
        mock_merge.assert_not_called()
        output = captured.getvalue()
        self.assertIn("Dry run", output)
        self.assertIn("[10, 11]", output)

    def test_apply_merges(self):
        db = _tmp_db()
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=10)
        db.stop_run(1)
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=11)
        db.stop_run(1)
        args = MagicMock(apply=True)
        captured = StringIO()
        with (
            patch("sys.stdout", captured),
            patch(NORMALIZE_MERGE) as mock_merge,
        ):
            cli.cmd_normalize(_registry(db, _client()), args)
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
        self.assertIn("No runs", captured.getvalue())

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
        db.create_run(1, "Bug", 10, "Project A", timesheet_id=1)
        run_id = db.get_active_run(1).id
        captured = StringIO()
        with (
            patch("sys.stdout", captured),
            patch("odoo_sdk.cli.__main__.OdooClient"),
            patch(ASSERT_GUARD),
            patch(STOP_GUARD),
            patch("odoo_sdk.cli.__main__.TaskStateDB", return_value=db),
            patch("sys.argv", ["odoo_sdk.cli", "stop", str(run_id)]),
        ):
            cli.main()
        self.assertIn("Stopped", captured.getvalue())


if __name__ == "__main__":
    unittest.main()
