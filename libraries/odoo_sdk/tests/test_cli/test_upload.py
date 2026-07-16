"""End-to-end tests for the headless ``odoo-sdk upload`` subcommand (issue #354).

Seeds a temporary state DB with events that derive into one session, then drives
``main`` with a recording fake Odoo client (mocked transport, no live Odoo), so
the whole pipeline runs for real: ``query_sessions`` derives from SQL,
``upload_sessions`` bills through ``reconcile_session`` (the sole hours-writer)
and runs the orphan sweep, and the idempotency ledger is written. The key
evidence is the shared-path test: the same seeded events driven through the TUI
``u`` path produce the identical ``account.analytic.line`` wire calls, proving a
non-interactive ``odoo-sdk upload`` bills exactly the rows the TUI would.
"""

import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import odoo_sdk.cli.__main__ as cli
from odoo_sdk.state import EventRecord, LocalStateClient
from tests.support import make_state_db

_MOD = "odoo_sdk.cli.__main__"
UTC = timezone.utc
GAP = 3600  # one hour, matching the seeded event spacing


def _seed_db() -> LocalStateClient:
    """Return a temp state DB seeded with two events forming one task session."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db = make_state_db(Path(tmp.name))
    base = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
    for offset in (0, GAP):  # exactly one gap apart -> a single derived session
        db.add_event(
            EventRecord(
                id=None,
                source="agent",
                timestamp=base + timedelta(seconds=offset),
                task_ids=["101"],
                repo="",
            )
        )
    return db


class _RecordingOdooClient:
    """Fake Odoo transport recording every call; anchors absent by design.

    ``search_read`` on ``account.analytic.line`` returns no anchor so the
    reconcile takes the create branch; ``project.task``/``hr.employee`` lookups
    are answered so project and employee resolution run for real; ``create``
    returns a scalar id (single-dict semantics).
    """

    def __init__(self) -> None:
        self.uid = 7
        self.calls: list[tuple] = []
        self._next_id = 500

    def execute(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((model, method, args, kwargs))
        if model == "hr.employee" and method == "search_read":
            return [{"id": 3}]
        if model == "project.task" and method == "read":
            return [{"id": args[0][0], "project_id": [9, "Proj"]}]
        if model == "account.analytic.line":
            if method == "search_read":
                return []  # no open anchor: force the fresh-line create branch
            if method == "create":
                new_id = self._next_id
                self._next_id += 1
                return new_id  # scalar, not [id]
            if method == "write":
                return True
        raise AssertionError(f"unexpected call: {model}.{method}")

    def analytic_calls(self) -> list[tuple]:
        return [c for c in self.calls if c[0] == "account.analytic.line"]


def _config() -> MagicMock:
    config = MagicMock()
    config.session_gap_secs = GAP
    # Real billing-policy floats so the shared upload path's minimum/rounding
    # (issue #355) can do arithmetic; the seeded 1h session is unaffected by the
    # defaults (1.0h rounds to 1.0h and clears the 0.25h floor).
    config.min_session_hours = 0.25
    config.round_session_hours = 0.05
    return config


def _run_cli(argv: list[str], db: LocalStateClient, client: _RecordingOdooClient) -> str:
    """Drive ``main`` for ``argv`` against the seeded DB and fake transport."""
    out = StringIO()
    with patch(f"{_MOD}.TaskStateDB", return_value=db), patch(
        f"{_MOD}._assert_env"
    ), patch(f"{_MOD}.OdooClient", return_value=client), patch(
        f"{_MOD}.LocalConfig"
    ) as local_config, patch("sys.stdout", out), patch(
        "sys.argv", ["odoo-sdk", *argv]
    ):
        local_config.load.return_value = _config()
        cli.main()
    return out.getvalue()


class TestCmdUpload(unittest.TestCase):
    def test_real_run_bills_the_derived_session(self):
        db, client = _seed_db(), _RecordingOdooClient()
        out = _run_cli(["upload"], db, client)

        # Exactly one billed line: created with the session's hours/identity.
        creates = [c for c in client.analytic_calls() if c[1] == "create"]
        self.assertEqual(len(creates), 1)
        vals = creates[0][2][0]
        self.assertEqual(vals["task_id"], 101)
        self.assertEqual(vals["project_id"], 9)
        self.assertEqual(vals["employee_id"], 3)
        self.assertEqual(vals["unit_amount"], 1.0)  # 3600s span -> 1.0h
        self.assertEqual(vals["name"], "[/] session 101|1")
        self.assertEqual(vals["date"], "2026-06-01")
        # The idempotency ledger maps the stable key to the created row.
        self.assertEqual(db.get_session_upload("101|1")["timesheet_id"], 500)
        self.assertIn("billed 1 session(s)", out)
        self.assertIn("-> timesheet 500", out)
        self.assertIn("task 101  101|1  1.00h", out)

    def test_rerun_is_idempotent_same_row(self):
        db, client = _seed_db(), _RecordingOdooClient()
        _run_cli(["upload"], db, client)
        _run_cli(["upload"], db, client)
        # The second run rewrote the mapped row instead of creating another.
        creates = [c for c in client.analytic_calls() if c[1] == "create"]
        writes = [c for c in client.analytic_calls() if c[1] == "write"]
        self.assertEqual(len(creates), 1)
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0][2][0], [500])  # the same, first-created row

    def test_dry_run_previews_without_writing(self):
        db, client = _seed_db(), _RecordingOdooClient()
        out = _run_cli(["upload", "--dry-run"], db, client)

        self.assertEqual(client.calls, [])  # not a single Odoo call
        self.assertIsNone(db.get_session_upload("101|1"))  # no ledger write
        self.assertIn("would bill 1 session(s)", out)
        self.assertIn("(dry run)", out)

    def test_date_range_filters_out_the_session(self):
        db, client = _seed_db(), _RecordingOdooClient()
        # A window entirely after the seeded session derives nothing to bill.
        out = _run_cli(
            ["upload", "--start", "2027-01-01", "--end", "2027-01-02"], db, client
        )
        self.assertEqual(client.analytic_calls(), [])
        self.assertIn("billed 0 session(s)", out)

    def test_upload_requires_odoo_env(self):
        # A real upload writes to Odoo, so the subcommand must stay behind the
        # global devcontainer assert (not in the local-only set).
        self.assertNotIn("upload", cli._LOCAL_ONLY)

    def test_invalid_date_rejected_cleanly(self):
        # A malformed --start/--end is an argparse usage error (exit 2, clean
        # message) rather than a raw ValueError traceback from date parsing.
        db, client = _seed_db(), _RecordingOdooClient()
        with self.assertRaises(SystemExit) as ctx:
            _run_cli(["upload", "--start", "2026-13-01"], db, client)
        self.assertEqual(ctx.exception.code, 2)
        self.assertEqual(client.calls, [])  # rejected before any Odoo work

    def test_cli_bills_identical_rows_to_the_tui_upload_path(self):
        # The acceptance proof for #354: the same seeded events driven through
        # the TUI 'u' loop and through headless `odoo-sdk upload` produce the
        # IDENTICAL account.analytic.line wire calls, because both surfaces
        # share the one billing.upload.upload_sessions loop.
        from odoo_sdk.commands import Registry
        from odoo_sdk.commands.builtin import register_builtins
        from odoo_sdk.tui.app import TuiDeps, _upload_sessions
        from odoo_sdk.tui.window import DateWindow

        cli_db, cli_client = _seed_db(), _RecordingOdooClient()
        _run_cli(["upload", "--start", "2026-06-01", "--end", "2026-06-07"],
                 cli_db, cli_client)

        tui_db, tui_client = _seed_db(), _RecordingOdooClient()
        registry = register_builtins(
            Registry(tui_client, state_client=tui_db, config=_config())
        )
        deps = TuiDeps(
            registry=registry, client=tui_client, store=tui_db, config=_config()
        )
        window = DateWindow(date(2026, 6, 1), date(2026, 6, 7))
        sessions = registry["query_sessions"].execute(
            start_date=window.start_iso(),
            end_date=window.end_iso(),
            include_events=True,
        )
        uploaded, retired = _upload_sessions(deps, sessions, window)

        self.assertEqual((uploaded, retired), (1, 0))
        self.assertEqual(cli_client.analytic_calls(), tui_client.analytic_calls())
        # Both ledgers map the same stable key onto the same row and hours
        # (uploaded_at is a wall-clock timestamp, so it is excluded).
        cli_map = cli_db.get_session_upload("101|1")
        tui_map = tui_db.get_session_upload("101|1")
        for field in ("session_key", "timesheet_id", "hours", "task_id"):
            self.assertEqual(cli_map[field], tui_map[field])


if __name__ == "__main__":
    unittest.main()
