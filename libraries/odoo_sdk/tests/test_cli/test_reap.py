"""Tests for the ``odoo-sdk reap`` subcommand and the stale-run attach exclusion (#366).

``reap`` bulk-aborts every stale run in the one host-provisioned central tracker
DB through the same local-abort path a single ``abort`` uses (stamp ``aborted_at``
so the run is excluded from billing, best-effort close the Odoo anchor). The
``--attach-active-run`` exclusion stops ``log-event`` from attaching new events to
a stale run, freezing its billable wall-clock.
"""

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import odoo_sdk.cli.__main__ as cli
from odoo_sdk.state import LocalStateClient
from odoo_sdk.state.db import tracker_db_path
from odoo_sdk.utilities.reap import REAP_THRESHOLD_ENV
from odoo_sdk.utilities.timesheet import ABORTED_ANCHOR_NAME, ANCHOR_NAME
from tests.support import provision_schema

ASSERT_GUARD = "odoo_sdk.cli.__main__.assert_odoo_devcontainer"


def _central_db(root: Path) -> LocalStateClient:
    db_path = tracker_db_path(root)
    provision_schema(db_path)
    return LocalStateClient(db_path=db_path)


def _backdate_run(root: Path, run_id: int, hours: float) -> None:
    """Backdate one run's ``started_at`` so it reads as stale."""
    conn = sqlite3.connect(str(tracker_db_path(root)))
    old = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    conn.execute("UPDATE task_runs SET started_at = ? WHERE id = ?", (old, run_id))
    conn.commit()
    conn.close()


def _anchor_client() -> MagicMock:
    """A client whose anchor rows all still carry the unreconciled marker."""
    client = MagicMock()

    def _execute(model, method, *args, **kwargs):
        if method == "read":
            return [{"id": args[0][0], "name": ANCHOR_NAME}]
        return True

    client.execute.side_effect = _execute
    return client


class _ReapEnv(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._prev = os.environ.get("ODOO_TASK_TRACKER_DIR")
        os.environ["ODOO_TASK_TRACKER_DIR"] = str(self.root)
        self.db = _central_db(self.root)

    def tearDown(self) -> None:
        if self._prev is None:
            os.environ.pop("ODOO_TASK_TRACKER_DIR", None)
        else:
            os.environ["ODOO_TASK_TRACKER_DIR"] = self._prev
        os.environ.pop(REAP_THRESHOLD_ENV, None)
        self._tmp.cleanup()

    def _reap(self, argv, client) -> str:
        out = StringIO()
        with (
            patch("sys.stdout", out),
            patch(ASSERT_GUARD),
            patch("odoo_sdk.cli.__main__.OdooClient", return_value=client),
            patch("sys.argv", ["odoo-sdk", *argv]),
        ):
            cli.main()
        return out.getvalue()

    def _log_event(self, argv) -> None:
        with (
            patch("sys.stdout", StringIO()),
            patch("odoo_sdk.cli.__main__.current_repo_label", return_value=""),
            patch("sys.argv", ["odoo-sdk", *argv]),
        ):
            cli.main()

    def _seed_fresh_and_stale(self):
        """Create one fresh RUNNING, one stale RUNNING, one stale AWAITING run."""
        fresh = self.db.create_run(1, "Fresh", 10, "Proj", 51)
        stale_running = self.db.create_run(2, "StaleRun", 10, "Proj", 52)
        stale_awaiting = self.db.create_run(3, "StaleWait", 10, "Proj", 53)
        self.db.transition_to_awaiting(3)
        _backdate_run(self.root, stale_running.id, 30)
        _backdate_run(self.root, stale_awaiting.id, 30)
        return fresh, stale_running, stale_awaiting


class TestReapCommand(_ReapEnv):
    def test_dry_run_lists_only_stale(self) -> None:
        _fresh, stale_running, stale_awaiting = self._seed_fresh_and_stale()
        out = self._reap(["reap", "--dry-run"], _anchor_client())
        self.assertIn("Would reap 2 stale run(s)", out)
        self.assertIn("StaleRun", out)
        self.assertIn("StaleWait", out)
        self.assertNotIn("Fresh", out)
        # Dry run must not have aborted anything.
        self.assertIsNone(self.db.get_run_by_id(stale_running.id).aborted_at)
        self.assertIsNone(self.db.get_run_by_id(stale_awaiting.id).aborted_at)

    def test_reap_aborts_only_stale_and_closes_anchors(self) -> None:
        fresh, stale_running, stale_awaiting = self._seed_fresh_and_stale()
        client = _anchor_client()
        out = self._reap(["reap"], client)
        self.assertIn("Reaped 2 stale run(s)", out)
        # Both stale runs are stopped and stamped; the fresh one is untouched.
        self.assertIsNotNone(self.db.get_run_by_id(stale_running.id).aborted_at)
        self.assertIsNotNone(self.db.get_run_by_id(stale_awaiting.id).aborted_at)
        self.assertIsNone(self.db.get_run_by_id(fresh.id).aborted_at)
        self.assertEqual(self.db.get_run_by_id(fresh.id).state.value, "RUNNING")
        # Each stale run's anchor was retired (safety check via read).
        for tid in (52, 53):
            client.execute.assert_any_call(
                "account.analytic.line",
                "write",
                [tid],
                {"name": ABORTED_ANCHOR_NAME, "unit_amount": 0.0},
            )

    def test_reap_is_idempotent(self) -> None:
        self._seed_fresh_and_stale()
        self._reap(["reap"], _anchor_client())
        out = self._reap(["reap"], _anchor_client())
        self.assertIn("No stale runs to reap", out)

    def test_reap_offline_still_stamps_aborted_at(self) -> None:
        _fresh, stale_running, _await = self._seed_fresh_and_stale()
        client = MagicMock()
        client.execute.side_effect = ConnectionError("odoo down")
        out = self._reap(["reap"], client)
        self.assertIn("Reaped 2 stale run(s)", out)
        # Best-effort anchor close failed offline, but the local abort stamped.
        self.assertIsNotNone(self.db.get_run_by_id(stale_running.id).aborted_at)

    def test_reap_recent_event_keeps_old_run_fresh(self) -> None:
        run = self.db.create_run(2, "Busy", 10, "Proj", 52)
        _backdate_run(self.root, run.id, 30)  # old start...
        self._log_event(  # ...but a recent event for its task
            ["log-event", "--source", "agent", "--task-id", "2"]
        )
        out = self._reap(["reap"], _anchor_client())
        self.assertIn("No stale runs to reap", out)
        self.assertIsNone(self.db.get_run_by_id(run.id).aborted_at)

    def test_no_runs_message(self) -> None:
        out = self._reap(["reap"], _anchor_client())
        self.assertIn("No stale runs to reap", out)

    def test_older_than_duration_units(self) -> None:
        self.assertEqual(cli._parse_reap_threshold("36"), 36.0)
        self.assertEqual(cli._parse_reap_threshold("36h"), 36.0)
        self.assertEqual(cli._parse_reap_threshold("2d"), 48.0)
        self.assertEqual(cli._parse_reap_threshold("1.5D"), 36.0)

    def test_invalid_older_than_exits_2(self) -> None:
        for bad in ("0", "-3", "abc", "inf", "nan"):
            with self.assertRaises(SystemExit) as ctx:
                with (
                    patch("sys.stderr", StringIO()),
                    patch("sys.stdout", StringIO()),
                    patch("sys.argv", ["odoo-sdk", "reap", "--older-than", bad]),
                ):
                    cli.main()
            self.assertEqual(ctx.exception.code, 2, bad)


class TestAttachExclusion(_ReapEnv):
    def test_stale_run_excluded_from_attachment(self) -> None:
        fresh, stale_running, stale_awaiting = self._seed_fresh_and_stale()
        self._log_event(
            ["log-event", "--source", "claude:PreToolUse", "--attach-active-run"]
        )
        events = self.db.get_events()
        # Only the fresh run's task id is attached; stale runs are excluded.
        self.assertEqual(events[-1].task_ids, ["1"])

    def test_env_override_widens_threshold(self) -> None:
        # With a 48h threshold, a 30h-old run is no longer stale and re-attaches.
        self._seed_fresh_and_stale()
        os.environ[REAP_THRESHOLD_ENV] = "48"
        self._log_event(
            ["log-event", "--source", "claude:PreToolUse", "--attach-active-run"]
        )
        events = self.db.get_events()
        self.assertEqual(sorted(events[-1].task_ids), ["1", "2", "3"])


if __name__ == "__main__":
    unittest.main()
