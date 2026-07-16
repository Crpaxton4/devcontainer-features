"""Aborted runs never bill: the #356 exclusion across abort, state, and upload.

Covers the whole seam: the ``task_runs.aborted_at`` column (part of the canonical
schema, stamped by :meth:`LocalStateClient.abort_run`), the
upload path's session-selection filter (a derived session lying wholly within an
aborted run's ``[started_at, aborted_at]`` window — upper bound widened by the
dispatch grace — is excluded from billing), and the end-to-end acceptance
sequence against a fixture DB and a stateful fake Odoo transport:
start → work → abort → upload bills 0 hours and the anchor reads
``[/] aborted stale run``; a later start → work → upload on the SAME task still
bills. The abort-dispatch agent event (emitted moments *after* ``aborted_at`` is
stamped, because the MCP wrapper and the hook shim both fire once the tool has
returned) is exercised specifically: it must not push the aborted session out of
the exclusion window.
"""

import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from odoo_sdk.commands.builtin.abort_task import AbortTaskCommand
from odoo_sdk.commands.builtin.query_sessions import QuerySessionsCommand
from odoo_sdk.state import EventRecord, LocalStateClient, TaskNotRunningError
from odoo_sdk.billing.timesheet import (
    ABORTED_ANCHOR_NAME,
    ANCHOR_NAME,
    ORPHANED_UPLOAD_NAME,
)
from odoo_sdk.billing.upload import (
    _ABORT_DISPATCH_GRACE,
    _within_aborted_window,
    upload_sessions,
)
from tests.support import make_state_db

UTC = timezone.utc
GAP = 3600
_ABORT_GUARD = "odoo_sdk.commands.builtin.abort_task.assert_odoo_devcontainer"

T_START = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
T_ABORT = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)
# The abort-dispatch agent event lands AFTER aborted_at is stamped (the MCP
# wrapper emits only once the tool has returned).
T_DISPATCH = T_ABORT + timedelta(seconds=2)
T_RESTART = datetime(2026, 6, 1, 11, 0, tzinfo=UTC)


def _tmp_db() -> LocalStateClient:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return make_state_db(Path(tmp.name))


def _config() -> MagicMock:
    """Hermetic config: fixed gap, billing policy disabled (raw hours bill).

    Passed explicitly to ``upload_sessions`` so the tests never fall back to
    ``LocalConfig.load()`` (which reads the real file/env) and the #355 billing
    policy cannot obscure the exclusion behavior under test.
    """
    config = MagicMock()
    config.session_gap_secs = GAP
    config.min_session_hours = 0.0
    config.round_session_hours = 0.0
    return config


@contextmanager
def _db_clock(when: datetime):
    """Freeze ``odoo_sdk.state.db``'s wall clock at ``when`` for run stamps."""
    with patch("odoo_sdk.state.db.datetime") as mock_dt:
        mock_dt.now.return_value = when
        mock_dt.fromisoformat.side_effect = datetime.fromisoformat
        yield


def _add_agent_event(db: LocalStateClient, when: datetime, subject: str) -> None:
    db.add_event(
        EventRecord(
            id=None,
            source="agent",
            timestamp=when,
            task_ids=["101"],
            repo="",
            subject=subject,
        )
    )


class _FakeOdoo:
    """Stateful fake Odoo transport: analytic lines live in ``self.rows``.

    Supports the exact calls the abort + upload paths issue: employee/project
    lookups, and ``read``/``search_read``/``create``/``write`` over
    ``account.analytic.line`` with real row state, so anchor renames and billed
    hours can be asserted on the resulting rows rather than on call shapes.
    """

    def __init__(self) -> None:
        self.uid = 7
        self.rows: dict[int, dict[str, Any]] = {}
        self._next_id = 500

    def seed_anchor(self, row_id: int, task_id: int) -> None:
        self.rows[row_id] = {
            "name": ANCHOR_NAME,
            "unit_amount": 0.0,
            "task_id": task_id,
            "project_id": 9,
        }

    def execute(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        if model == "hr.employee" and method == "search_read":
            return [{"id": 3}]
        if model == "project.task" and method == "read":
            return [{"id": args[0][0], "project_id": [9, "Proj"]}]
        if model == "account.analytic.line":
            return self._analytic(method, args, kwargs)
        raise AssertionError(f"unexpected call: {model}.{method}")

    def _analytic(self, method: str, args: tuple, kwargs: dict) -> Any:
        if method == "read":
            row = self.rows.get(args[0][0])
            return [] if row is None else [{"id": args[0][0], **row}]
        if method == "search_read":
            hits = [
                {"id": rid}
                for rid, row in sorted(self.rows.items())
                if all(row.get(field) == value for field, _, value in args[0])
            ]
            return hits[: kwargs.get("limit") or len(hits)]
        if method == "create":
            rid = self._next_id
            self._next_id += 1
            self.rows[rid] = dict(args[0])
            return rid
        if method == "write":
            for rid in args[0]:
                self.rows[rid].update(args[1])
            return True
        raise AssertionError(f"unexpected analytic method: {method}")


class TestAbortRunStateMethod(unittest.TestCase):
    def test_abort_run_stamps_aborted_at_and_stops(self):
        db = _tmp_db()
        with _db_clock(T_START):
            db.create_run(101, "Bug", 9, "Proj", timesheet_id=50)
        with _db_clock(T_ABORT):
            run = db.abort_run(101)
        self.assertEqual(run.aborted_at, T_ABORT)
        self.assertEqual(run.stopped_at, T_ABORT)
        self.assertEqual(run.state.value, "STOPPED")

    def test_stop_run_leaves_aborted_at_null(self):
        db = _tmp_db()
        db.create_run(101, "Bug", 9, "Proj")
        run = db.stop_run(101)
        self.assertIsNone(run.aborted_at)
        self.assertEqual(db.get_aborted_runs(), [])

    def test_get_aborted_runs_returns_only_aborted(self):
        db = _tmp_db()
        db.create_run(101, "Bug", 9, "Proj")
        db.stop_run(101)
        with _db_clock(T_START):
            db.create_run(102, "Other", 9, "Proj")
        with _db_clock(T_ABORT):
            db.abort_run(102)
        aborted = db.get_aborted_runs()
        self.assertEqual(len(aborted), 1)
        self.assertEqual(aborted[0].task_id, 102)
        self.assertEqual(aborted[0].aborted_at, T_ABORT)

    def test_abort_run_raises_when_no_active_run(self):
        db = _tmp_db()
        with self.assertRaises(TaskNotRunningError) as ctx:
            db.abort_run(999)
        self.assertEqual(
            str(ctx.exception), "No active session found for task 999."
        )


class TestAbortedAtColumn(unittest.TestCase):
    def test_provisioned_db_carries_nullable_aborted_at(self):
        # The canonical schema (#369) already carries ``aborted_at``, so a fresh
        # run reads NULL (still billable) and abort stamps it — no migration.
        db = make_state_db()
        db.create_run(101, "Old", 9, "Proj", timesheet_id=1)
        run = db.get_active_run(101)
        self.assertIsNotNone(run)
        self.assertIsNone(run.aborted_at)
        with _db_clock(T_ABORT):
            aborted = db.abort_run(101)
        self.assertEqual(aborted.aborted_at, T_ABORT)


def _session(started: datetime, ended: datetime, task_id: str = "101") -> dict:
    return {
        "session_key": f"{task_id}|1",
        "task_id": task_id,
        "duration_secs": (ended - started).total_seconds(),
        "started_at": started.isoformat(),
        "ended_at": ended.isoformat(),
    }


class TestWithinAbortedWindow(unittest.TestCase):
    WINDOWS = [("101", T_START, T_ABORT)]

    def test_session_wholly_inside_window_is_excluded(self):
        session = _session(T_START, T_ABORT - timedelta(minutes=5))
        self.assertTrue(_within_aborted_window(session, self.WINDOWS))

    def test_dispatch_event_at_grace_edge_is_still_covered(self):
        # ended exactly aborted_at + grace: inclusive, still excluded.
        session = _session(T_START, T_ABORT + _ABORT_DISPATCH_GRACE)
        self.assertTrue(_within_aborted_window(session, self.WINDOWS))

    def test_session_past_the_grace_straddles_and_bills(self):
        # A gap-chain reaching into post-restart work is NOT excluded.
        session = _session(
            T_START, T_ABORT + _ABORT_DISPATCH_GRACE + timedelta(seconds=1)
        )
        self.assertFalse(_within_aborted_window(session, self.WINDOWS))

    def test_other_task_is_never_excluded(self):
        session = _session(T_START, T_ABORT - timedelta(minutes=5), task_id="202")
        self.assertFalse(_within_aborted_window(session, self.WINDOWS))

    def test_session_started_before_the_run_is_not_excluded(self):
        session = _session(T_START - timedelta(hours=1), T_ABORT)
        self.assertFalse(_within_aborted_window(session, self.WINDOWS))

    def test_short_work_started_after_the_abort_still_bills(self):
        # A fresh run's session begins AFTER aborted_at, so even sub-grace
        # post-restart work is never swallowed by the exclusion window.
        started = T_ABORT + timedelta(seconds=10)
        session = _session(started, started + timedelta(seconds=30))
        self.assertFalse(_within_aborted_window(session, self.WINDOWS))

    def test_naive_legacy_session_bounds_are_treated_as_utc(self):
        # Events stored before the +00:00 normalization parse naive; the
        # filter must compare them as UTC instead of raising TypeError.
        session = _session(T_START, T_ABORT - timedelta(minutes=5))
        session["started_at"] = session["started_at"].replace("+00:00", "")
        session["ended_at"] = session["ended_at"].replace("+00:00", "")
        self.assertTrue(_within_aborted_window(session, self.WINDOWS))


class TestUploadSelectionFilter(unittest.TestCase):
    """The shared loop drops aborted-window sessions before billing and sweep."""

    def _db_with_aborted_run(self) -> LocalStateClient:
        db = _tmp_db()
        with _db_clock(T_START):
            db.create_run(101, "Bug", 9, "Proj", timesheet_id=50)
        with _db_clock(T_ABORT):
            db.abort_run(101)
        return db

    def test_aborted_session_is_excluded_and_kept_out_of_the_sweep(self):
        db = self._db_with_aborted_run()
        aborted_session = _session(T_START, T_DISPATCH)
        live_session = {
            **_session(T_RESTART, T_RESTART + timedelta(minutes=30)),
            "session_key": "101|9",
        }
        with patch(
            "odoo_sdk.billing.upload.reconcile_session", return_value=700
        ) as reconcile, patch(
            "odoo_sdk.billing.upload.sweep_orphaned_uploads", return_value=0
        ) as sweep:
            result = upload_sessions(
                MagicMock(),
                db,
                [aborted_session, live_session],
                start_date="2026-06-01",
                end_date="2026-06-01",
                config=_config(),
            )
        self.assertEqual(result["uploaded"], 1)
        self.assertEqual(result["excluded"], 1)
        self.assertEqual(reconcile.call_count, 1)
        self.assertEqual(reconcile.call_args.args[3], "101|9")  # live one only
        # The excluded session is absent from the sweep's derived KEY set (so
        # any hours a pre-abort upload wrote for it are retired as orphans),
        # but its task stays in the derived TASK set so a legacy NULL-bounds
        # ledger row for the aborted task is still retired.
        self.assertEqual(sweep.call_args.kwargs["derived_keys"], {"101|9"})
        self.assertEqual(sweep.call_args.kwargs["derived_task_ids"], {"101"})

    def test_legacy_ledger_row_for_the_aborted_task_is_retired(self):
        # A pre-#353 mapping (NULL bounds) for the aborted run must also be
        # zeroed: the aborted task's id stays in derived_task_ids even though
        # its only session is excluded, so the legacy sweep branch fires.
        db = self._db_with_aborted_run()
        client = _FakeOdoo()
        client.rows[500] = {
            "name": "[/] session 101|1",
            "unit_amount": 1.0,
            "task_id": 101,
        }
        db.record_session_upload("101|1", 500, 1.0)  # legacy: no bounds/task
        result = upload_sessions(
            client,
            db,
            [_session(T_START, T_DISPATCH)],
            start_date="2026-06-01",
            end_date="2026-06-01",
            config=_config(),
        )
        self.assertEqual(result["excluded"], 1)
        self.assertEqual(result["retired"], 1)
        self.assertEqual(client.rows[500]["unit_amount"], 0.0)
        self.assertIsNone(db.get_session_upload("101|1"))

    def test_dry_run_previews_the_same_exclusion(self):
        db = self._db_with_aborted_run()
        with patch("odoo_sdk.billing.upload.reconcile_session") as reconcile:
            result = upload_sessions(
                MagicMock(),
                db,
                [_session(T_START, T_DISPATCH)],
                dry_run=True,
                config=_config(),
            )
        reconcile.assert_not_called()
        self.assertEqual(result["excluded"], 1)
        self.assertEqual(result["uploaded"], 0)


class TestAbortNeverBillsEndToEnd(unittest.TestCase):
    """Acceptance for #356 against a fixture DB and a stateful fake transport."""

    def _query(self, client, db) -> list[dict]:
        return QuerySessionsCommand(client, state=db, config=_config()).execute(
            start_date="2026-06-01", end_date="2026-06-02", include_events=False
        )

    def _upload(self, client, db) -> dict:
        return upload_sessions(
            client,
            db,
            self._query(client, db),
            start_date="2026-06-01",
            end_date="2026-06-02",
            config=_config(),
        )

    def test_start_work_abort_upload_bills_zero_then_restart_bills(self):
        db, client = _tmp_db(), _FakeOdoo()
        client.seed_anchor(50, task_id=101)

        # start → work → abort (dispatch event lands AFTER aborted_at).
        with _db_clock(T_START):
            db.create_run(101, "Bug", 9, "Proj", timesheet_id=50)
        _add_agent_event(db, T_START, "task_status")
        _add_agent_event(db, T_START + timedelta(minutes=10), "task_note")
        with patch(_ABORT_GUARD), _db_clock(T_ABORT):
            abort_result = AbortTaskCommand(client, state=db).execute(101)
        _add_agent_event(db, T_DISPATCH, "abort_task")

        # The Odoo anchor is retired: renamed, still 0 hours, never deleted.
        self.assertTrue(abort_result["anchor_closed"])
        self.assertEqual(client.rows[50]["name"], ABORTED_ANCHOR_NAME)
        self.assertEqual(client.rows[50]["unit_amount"], 0.0)

        # upload → the aborted run's session (which the trailing dispatch event
        # extended past aborted_at) bills nothing: 0 uploads, no new rows.
        result = self._upload(client, db)
        self.assertEqual(result["uploaded"], 0)
        self.assertEqual(result["excluded"], 1)
        self.assertEqual(set(client.rows), {50})  # no billed line created
        self.assertEqual(client.rows[50]["unit_amount"], 0.0)

        # start again on the SAME task → work → stop → upload DOES bill.
        with _db_clock(T_RESTART):
            db.create_run(101, "Bug", 9, "Proj", timesheet_id=None)
        _add_agent_event(db, T_RESTART, "task_status")
        _add_agent_event(db, T_RESTART + timedelta(minutes=30), "task_note")
        with _db_clock(T_RESTART + timedelta(minutes=45)):
            db.stop_run(101)

        result = self._upload(client, db)
        self.assertEqual(result["uploaded"], 1)
        self.assertEqual(result["excluded"], 1)  # the aborted one stays out
        billed_id = result["rows"][0]["timesheet_id"]
        self.assertNotEqual(billed_id, 50)  # a fresh line, not the retired anchor
        self.assertEqual(client.rows[billed_id]["unit_amount"], 0.5)  # 30 min
        self.assertEqual(client.rows[billed_id]["task_id"], 101)
        self.assertEqual(client.rows[50]["name"], ABORTED_ANCHOR_NAME)  # untouched
        self.assertIsNotNone(db.get_session_upload(result["rows"][0]["session_key"]))

    def test_hours_uploaded_before_the_abort_are_zeroed_by_the_sweep(self):
        db, client = _tmp_db(), _FakeOdoo()
        with _db_clock(T_START):
            db.create_run(101, "Bug", 9, "Proj", timesheet_id=None)
        _add_agent_event(db, T_START, "task_status")
        _add_agent_event(db, T_START + timedelta(minutes=10), "task_note")

        # Upload BEFORE the abort: the session bills onto a fresh line.
        result = self._upload(client, db)
        self.assertEqual(result["uploaded"], 1)
        billed_id = result["rows"][0]["timesheet_id"]
        self.assertGreater(client.rows[billed_id]["unit_amount"], 0.0)

        # Abort, then upload again: the session is now excluded, so the sweep
        # retires the pre-abort mapping and zeroes its row — abort un-bills.
        with patch(_ABORT_GUARD), _db_clock(T_ABORT):
            AbortTaskCommand(client, state=db).execute(101)
        _add_agent_event(db, T_DISPATCH, "abort_task")
        result = self._upload(client, db)
        self.assertEqual(result["uploaded"], 0)
        self.assertEqual(result["excluded"], 1)
        self.assertEqual(result["retired"], 1)
        self.assertEqual(client.rows[billed_id]["unit_amount"], 0.0)
        self.assertEqual(client.rows[billed_id]["name"], ORPHANED_UPLOAD_NAME)
        self.assertIsNone(db.get_session_upload("101|1"))  # mapping retired


if __name__ == "__main__":
    unittest.main()
