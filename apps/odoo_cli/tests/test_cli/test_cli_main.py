"""Tests for odoo_cli.__main__ CLI companion."""

import unittest
from datetime import datetime, timezone
from io import StringIO
from unittest.mock import MagicMock, patch

from odoo_dev_data.config import LocalConfig
from odoo_dev_data.events import EventStore as TaskStateDB

from odoo_cli.__main__ import main
from odoo_cli.commands import run_get_employee_id, run_normalize

from ..support import invoke, make_event_store, make_service, serving


def _tmp_db() -> TaskStateDB:
    return make_event_store()


def _client() -> MagicMock:
    return MagicMock()


def _service(db: TaskStateDB, client: MagicMock = None):
    """Build the shared command service over ``db`` for direct cmd_* calls."""
    return make_service(db, client if client is not None else MagicMock())


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


NORMALIZE_MERGE = "odoo_dev_core.builtins.normalize_timesheets.merge_timesheets"


# ── the Odoo configuration guard ──────────────────────────────────────────────


class TestOdooGuard(unittest.TestCase):
    def test_exits_when_connection_settings_missing(self):
        # The guard's failure (#642) is a one-line error on stderr and exit 1.
        err = StringIO()
        service = make_service(config=LocalConfig())
        with serving(service), patch("sys.stderr", err):
            code = main(["normalize"])
        self.assertEqual(code, 1)
        self.assertIn("Missing Odoo connection settings", err.getvalue())

    def test_passes_when_sdk_configured(self):
        with serving(_service(_tmp_db())), patch("sys.stdout", StringIO()):
            self.assertEqual(main(["normalize"]), 0)


# ── cmd_normalize ─────────────────────────────────────────────────────────────


class TestCmdNormalize(unittest.TestCase):
    def test_no_duplicates_message(self):
        db = _tmp_db()
        args = MagicMock(apply=False)
        captured = StringIO()
        with patch("sys.stdout", captured):
            run_normalize(_service(db, _client()), args)
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
            run_normalize(_service(db, _client()), args)
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
            run_normalize(_service(db, _client()), args)
        mock_merge.assert_called_once()
        self.assertIn("Merged", captured.getvalue())
        # The upload ledger now maps both sessions onto the primary timesheet.
        self.assertEqual(
            {row["timesheet_id"] for row in db.list_session_uploads()}, {10}
        )


# ── cmd_get_employee_id (U15 fold-in) ─────────────────────────────────────────

_RESOLVE_EMP = "odoo_dev_core.builtins.get_employee_id.resolve_employee_id"


class TestCmdGetEmployeeId(unittest.TestCase):
    def test_prints_employee_id(self):
        db = _tmp_db()
        service = _service(db, _client())
        captured = StringIO()
        with (
            patch("sys.stdout", captured),
            patch(_RESOLVE_EMP, return_value=77),
        ):
            run_get_employee_id(service, MagicMock())
        self.assertIn("employee_id=77", captured.getvalue())


# ── main entrypoint ───────────────────────────────────────────────────────────


class TestMain(unittest.TestCase):
    def _run_main(self, argv, db=None):
        if db is None:
            db = _tmp_db()
        with serving(_service(db)), patch("sys.argv", ["odoo_cli"] + argv):
            invoke()

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
        with patch("sys.stdout", captured):
            self._run_main(["normalize"])
        self.assertIn("No duplicate", captured.getvalue())

    def test_get_employee_id_command(self):
        # get-employee-id needs Odoo; the resolver is stubbed so no RPC happens.
        captured = StringIO()
        with patch("sys.stdout", captured), patch(_RESOLVE_EMP, return_value=77):
            self._run_main(["get-employee-id"])
        self.assertIn("employee_id=77", captured.getvalue())

    def test_version_prints_the_distribution_version(self):
        captured = StringIO()
        with patch("sys.stdout", captured):
            self.assertEqual(main(["--version"]), 0)
        self.assertRegex(captured.getvalue(), r"^odoo-dev \d+\.\d+")


if __name__ == "__main__":
    unittest.main()
