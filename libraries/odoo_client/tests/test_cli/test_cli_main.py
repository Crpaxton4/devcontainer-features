"""Tests for odoo_client.cli.__main__ CLI companion."""

import unittest
from datetime import datetime, timezone
from io import StringIO
from unittest.mock import MagicMock, patch

import odoo_client.cli.__main__ as cli
from odoo_dev_data.events import EventStore as TaskStateDB
from tests.support import make_event_store

ASSERT_GUARD = "odoo_client.cli.__main__.assert_configured"


def _tmp_db() -> TaskStateDB:
    return make_event_store()


def _client() -> MagicMock:
    return MagicMock()


def _registry(db: TaskStateDB, client: MagicMock = None):
    """Build the shared built-in registry over ``db`` for direct cmd_* calls."""
    from odoo_client.commands import Registry
    from odoo_client.commands.builtin import register_builtins

    return register_builtins(
        Registry(client if client is not None else MagicMock(), state_client=db)
    )


def _record_upload(db: TaskStateDB, key: str, timesheet_id: int, hour: int) -> None:
    """Record one uploaded session of task ``1`` on a fixed day."""
    db.record_session_upload(
        key,
        timesheet_id,
        1.0,
        task_id="1",
        started_at=datetime(2026, 6, 1, hour, 0, tzinfo=timezone.utc),
        ended_at=datetime(2026, 6, 1, hour + 1, 0, tzinfo=timezone.utc),
    )


NORMALIZE_MERGE = "odoo_client.commands.builtin.normalize_timesheets.merge_timesheets"


# ── _assert_env ───────────────────────────────────────────────────────────────


class TestAssertEnv(unittest.TestCase):
    def test_exits_when_connection_settings_missing(self):
        # The guard's second failure mode (#642) must exit 1 the same way.
        with patch(ASSERT_GUARD, side_effect=ValueError("Missing Odoo connection")):
            with self.assertRaises(SystemExit) as ctx:
                cli._assert_env()
        self.assertEqual(ctx.exception.code, 1)

    def test_passes_when_sdk_configured(self):
        with patch(ASSERT_GUARD):
            cli._assert_env()  # must not raise


# ── _LazyOdooClient ───────────────────────────────────────────────────────────


class TestLazyOdooClient(unittest.TestCase):
    def test_defers_construction_until_first_use(self):
        with patch("odoo_client.cli.__main__.build_client") as make_client:
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
        _record_upload(db, "1|10", 10, 9)
        _record_upload(db, "1|11", 11, 13)
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
        _record_upload(db, "1|10", 10, 9)
        _record_upload(db, "1|11", 11, 13)
        args = MagicMock(apply=True)
        captured = StringIO()
        with (
            patch("sys.stdout", captured),
            patch(NORMALIZE_MERGE) as mock_merge,
        ):
            cli.cmd_normalize(_registry(db, _client()), args)
        mock_merge.assert_called_once()
        self.assertIn("Merged", captured.getvalue())
        # The upload ledger now maps both sessions onto the primary timesheet.
        self.assertEqual(
            {row["timesheet_id"] for row in db.list_session_uploads()}, {10}
        )


# ── cmd_get_employee_id (U15 fold-in) ─────────────────────────────────────────

_RESOLVE_EMP = "odoo_client.commands.builtin.get_employee_id.resolve_employee_id"


class TestCmdGetEmployeeId(unittest.TestCase):
    def test_prints_employee_id(self):
        db = _tmp_db()
        registry = _registry(db, _client())
        captured = StringIO()
        with (
            patch("sys.stdout", captured),
            patch(_RESOLVE_EMP, return_value=77),
        ):
            cli.cmd_get_employee_id(registry, MagicMock())
        self.assertIn("employee_id=77", captured.getvalue())


# ── main entrypoint ───────────────────────────────────────────────────────────


class TestMain(unittest.TestCase):
    def _run_main(self, argv, db=None):
        if db is None:
            db = _tmp_db()
        with (
            patch(ASSERT_GUARD),
            patch("odoo_client.cli.__main__.TaskStateDB", return_value=db),
            patch("sys.argv", ["odoo_client.cli"] + argv),
        ):
            cli.main()

    def test_no_command_prints_help_and_exits(self):
        captured = StringIO()
        with patch("sys.stdout", captured):
            with self.assertRaises(SystemExit) as ctx:
                self._run_main([])
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("usage", captured.getvalue().lower())

    def test_removed_run_subcommands_are_rejected(self):
        for removed in ("list", "stop", "stop-all", "report", "abort", "close"):
            with self.subTest(command=removed):
                with patch("sys.stderr", StringIO()):
                    with self.assertRaises(SystemExit) as ctx:
                        self._run_main([removed])
                self.assertEqual(ctx.exception.code, 2)

    def test_normalize_command(self):
        captured = StringIO()
        with (
            patch("sys.stdout", captured),
            patch("odoo_client.cli.__main__.build_client"),
        ):
            self._run_main(["normalize"])
        self.assertIn("No duplicate", captured.getvalue())

    def test_get_employee_id_command(self):
        # get-employee-id needs Odoo; the resolver is stubbed so no RPC happens.
        db = _tmp_db()
        captured = StringIO()
        with (
            patch("sys.stdout", captured),
            patch(ASSERT_GUARD),
            patch("odoo_client.cli.__main__.build_client"),
            patch("odoo_client.cli.__main__.TaskStateDB", return_value=db),
            patch(_RESOLVE_EMP, return_value=77),
            patch("sys.argv", ["odoo_client.cli", "get-employee-id"]),
        ):
            cli.main()
        self.assertIn("employee_id=77", captured.getvalue())


if __name__ == "__main__":
    unittest.main()
