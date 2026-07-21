"""Tests for the shared derived-session upload loop (issue #354).

This is the single upload path both the TUI ``u`` key and the headless
``odoo-sdk upload`` subcommand delegate to. The tests exercise the loop's
selection (numeric task ids only), the per-session ``reconcile_session``
inputs, the orphan-sweep wiring (window scoping, skip on dry run), the
``dry_run`` preview, the ``range_bounds`` date semantics, and the summary
shape. The hours-writer and sweep are patched at the module boundary so the
billed rows are asserted at the call level without a live Odoo; the full write
path runs against a mocked transport in ``tests/test_cli/test_upload.py``.
"""

import unittest
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

from odoo_sdk.commands.builtin.query_sessions import QuerySessionsCommand
from odoo_sdk.state import EventRecord
from odoo_sdk.state.config import LocalConfig
from odoo_sdk.transport.errors import OdooServerError, OdooTransportError
from odoo_sdk.billing.upload import (
    range_bounds,
    upload_sessions,
    _numeric_task_id,
)
from tests.support import make_state_db

_MOD = "odoo_sdk.billing.upload"
_RECONCILE = f"{_MOD}.reconcile_session"
_SWEEP = f"{_MOD}.sweep_orphaned_uploads"


def _sessions(n=2):
    return [
        {
            "session_id": i,
            "session_key": f"{100 + i}|{i}",
            "task_id": str(100 + i),
            "duration_secs": 3600,
            "started_at": "2026-06-01T09:00:00+00:00",
            "ended_at": "2026-06-01T10:00:00+00:00",
            "events": [],
        }
        for i in range(n)
    ]


class TestNumericTaskId(unittest.TestCase):
    def test_parses_numeric_ids(self):
        self.assertEqual(_numeric_task_id("42"), 42)
        self.assertEqual(_numeric_task_id(7), 7)

    def test_rejects_non_numeric(self):
        self.assertIsNone(_numeric_task_id("UNKNOWN"))
        self.assertIsNone(_numeric_task_id(None))


class TestRangeBounds(unittest.TestCase):
    def test_inclusive_dates_cover_whole_end_day(self):
        lo, hi = range_bounds("2026-06-01", "2026-06-03")
        self.assertEqual(lo, datetime(2026, 6, 1, 0, 0))
        self.assertEqual(hi, datetime(2026, 6, 4, 0, 0))  # midnight after end

    def test_omitted_bounds_default_to_widest_range(self):
        lo, hi = range_bounds(None, None)
        self.assertEqual(lo, datetime.min)
        self.assertEqual(hi, datetime.max)

    def test_start_only_leaves_end_unbounded(self):
        lo, hi = range_bounds("2026-06-01", None)
        self.assertEqual(lo, datetime(2026, 6, 1, 0, 0))
        self.assertEqual(hi, datetime.max)


class TestUploadSessionsLoop(unittest.TestCase):
    def test_reconciles_once_per_numeric_session(self):
        with patch(_RECONCILE, return_value=500) as reconcile, patch(
            _SWEEP, return_value=0
        ):
            result = upload_sessions(MagicMock(), MagicMock(), _sessions(3))
        self.assertEqual(reconcile.call_count, 3)
        self.assertEqual(result["uploaded"], 3)
        self.assertEqual(result["skipped"], 0)

    def test_non_numeric_task_skipped(self):
        sessions = _sessions(1)
        sessions.append({**sessions[0], "task_id": "UNKNOWN", "session_id": 9})
        with patch(_RECONCILE, return_value=1) as reconcile, patch(
            _SWEEP, return_value=0
        ):
            result = upload_sessions(MagicMock(), MagicMock(), sessions)
        self.assertEqual(reconcile.call_count, 1)  # only the numeric one is billed
        self.assertEqual(result["uploaded"], 1)
        self.assertEqual(result["skipped"], 1)

    def test_reconcile_receives_identity_hours_and_bounds(self):
        sessions = [
            {
                "session_key": "100|5",
                "task_id": "100",
                "duration_secs": 7200,
                "started_at": "2026-06-01T09:00:00+00:00",
                "ended_at": "2026-06-01T11:00:00+00:00",
            }
        ]
        with patch(_RECONCILE, return_value=77) as reconcile, patch(
            _SWEEP, return_value=0
        ):
            result = upload_sessions(MagicMock(), MagicMock(), sessions)
        args = reconcile.call_args.args
        self.assertEqual(args[2], 100)  # numeric task id
        self.assertEqual(args[3], "100|5")  # stable session key
        self.assertEqual(args[4], "[/] session 100|5")  # description
        self.assertEqual(args[5], 2.0)  # 7200s -> 2.0h
        self.assertEqual(args[6], datetime.fromisoformat("2026-06-01T09:00:00+00:00"))
        self.assertEqual(args[7], datetime.fromisoformat("2026-06-01T11:00:00+00:00"))
        # The summary row records the id the hours-writer returned.
        self.assertEqual(result["rows"][0]["timesheet_id"], 77)
        self.assertEqual(result["rows"][0]["hours"], 2.0)

    def test_sweep_runs_after_upload_with_window_scope(self):
        # The stale-mapping sweep (#353) runs inside the shared path so both
        # the TUI and the headless CLI get it, scoped to the queried range
        # (bounds resolved internally via range_bounds so the query window and
        # the sweep window cannot drift) and keyed on the just-derived key set.
        sessions = _sessions(2)
        with patch(_RECONCILE, return_value=1), patch(
            _SWEEP, return_value=2
        ) as sweep:
            result = upload_sessions(
                MagicMock(),
                MagicMock(),
                sessions,
                start_date="2026-06-01",
                end_date="2026-06-03",
            )
        self.assertEqual(result["retired"], 2)
        kwargs = sweep.call_args.kwargs
        self.assertEqual(kwargs["derived_keys"], {"100|0", "101|1"})
        self.assertEqual(kwargs["derived_task_ids"], {"100", "101"})
        self.assertEqual(kwargs["window_lo"], datetime(2026, 6, 1, 0, 0))
        self.assertEqual(kwargs["window_hi"], datetime(2026, 6, 4, 0, 0))

    def test_dry_run_writes_nothing_but_summarises(self):
        with patch(_RECONCILE) as reconcile, patch(_SWEEP) as sweep:
            result = upload_sessions(
                MagicMock(), MagicMock(), _sessions(2), dry_run=True
            )
        reconcile.assert_not_called()  # no Odoo write on a dry run
        sweep.assert_not_called()  # the sweep also writes, so it is skipped too
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["uploaded"], 2)  # still counts the billable set
        self.assertEqual(result["retired"], 0)
        self.assertIsNone(result["rows"][0]["timesheet_id"])  # nothing written


class TestPerSessionIsolation(unittest.TestCase):
    """#582/#576: one failing session must not abort the batch or skip the sweep.

    The historical bug: ``reconcile_session`` raised out of the plain ``for``
    loop, so the first server fault (e.g. #576's "At least one analytic account
    must be set" ``UserError``) aborted every remaining session AND skipped the
    orphan sweep, leaving a silent partial upload. The loop now isolates each
    session, collects a structured ``failed`` row, and still runs the sweep.
    """

    def _analytic_fault(self, bad_key: str):
        """A reconcile side effect that faults for one session key, else bills."""

        def _reconcile(client, state, task_id, key, *rest):
            if key == bad_key:
                raise OdooServerError(
                    "At least one analytic account must be set",
                    model="account.analytic.line",
                    method="create",
                )
            return 500

        return _reconcile

    def test_one_fault_does_not_abort_the_batch(self):
        sessions = _sessions(3)  # keys 100|0, 101|1, 102|2
        with patch(_RECONCILE, side_effect=self._analytic_fault("101|1")), patch(
            _SWEEP, return_value=0
        ):
            result = upload_sessions(MagicMock(), MagicMock(), sessions)
        # The two healthy sessions still billed despite the middle one faulting.
        self.assertEqual(result["uploaded"], 2)
        self.assertEqual(len(result["rows"]), 2)
        self.assertEqual({r["session_key"] for r in result["rows"]}, {"100|0", "102|2"})

    def test_fault_is_captured_as_a_structured_failure_row(self):
        sessions = _sessions(2)  # keys 100|0, 101|1
        with patch(_RECONCILE, side_effect=self._analytic_fault("101|1")), patch(
            _SWEEP, return_value=0
        ):
            result = upload_sessions(MagicMock(), MagicMock(), sessions)
        self.assertEqual(len(result["failed"]), 1)
        failure = result["failed"][0]
        self.assertEqual(failure["session_key"], "101|1")
        self.assertEqual(failure["task_id"], 101)
        self.assertEqual(failure["error"], "At least one analytic account must be set")

    def test_sweep_still_runs_and_keeps_the_failed_key(self):
        # #582: the sweep must still run over the successfully-processed keys, and
        # the failed session's key stays in ``derived_keys`` so a partially-billed
        # row for it is never zeroed as an orphan.
        sessions = _sessions(2)
        with patch(_RECONCILE, side_effect=self._analytic_fault("101|1")), patch(
            _SWEEP, return_value=3
        ) as sweep:
            result = upload_sessions(
                MagicMock(), MagicMock(), sessions,
                start_date="2026-06-01", end_date="2026-06-03",
            )
        self.assertEqual(result["retired"], 3)
        sweep.assert_called_once()
        derived_keys = sweep.call_args.kwargs["derived_keys"]
        self.assertIn("100|0", derived_keys)  # the billed one
        self.assertIn("101|1", derived_keys)  # the failed one — not swept

    def test_all_sessions_faulting_returns_partial_not_raises(self):
        sessions = _sessions(2)
        with patch(
            _RECONCILE, side_effect=OdooTransportError("connection reset")
        ), patch(_SWEEP, return_value=0):
            result = upload_sessions(MagicMock(), MagicMock(), sessions)
        self.assertEqual(result["uploaded"], 0)
        self.assertEqual(result["rows"], [])
        self.assertEqual(len(result["failed"]), 2)
        self.assertTrue(all("connection reset" in f["error"] for f in result["failed"]))

    def test_no_failures_leaves_failed_list_empty(self):
        with patch(_RECONCILE, return_value=1), patch(_SWEEP, return_value=0):
            result = upload_sessions(MagicMock(), MagicMock(), _sessions(2))
        self.assertEqual(result["failed"], [])


class TestSharedPathWithTui(unittest.TestCase):
    """The TUI and a headless caller bill identically: one shared loop."""

    def test_tui_delegation_and_direct_call_bill_identically(self):
        from odoo_sdk.tui.app import TuiDeps, _upload_sessions
        from odoo_sdk.tui.window import DateWindow

        sessions = _sessions(3)
        window = DateWindow(date(2026, 6, 1), date(2026, 6, 3))
        client, state = MagicMock(), MagicMock()
        # The driver forwards its own injected (client, store) pair to the shared
        # loop — no reaching into a command's private ``._client``/``.state``.
        deps = TuiDeps(
            registry={}, client=client, store=state, config=MagicMock()
        )

        # The TUI 'u' path routes through the shared loop.
        with patch(_RECONCILE, return_value=1) as reconcile, patch(
            _SWEEP, return_value=0
        ) as sweep:
            uploaded, retired = _upload_sessions(deps, sessions, window)
        tui_calls = [c.args for c in reconcile.call_args_list]
        tui_sweep = sweep.call_args.kwargs

        # A direct headless invocation of the same loop, same inputs.
        with patch(_RECONCILE, return_value=1) as reconcile, patch(
            _SWEEP, return_value=0
        ) as sweep:
            upload_sessions(
                client, state, sessions,
                start_date="2026-06-01", end_date="2026-06-03",
            )
        cli_calls = [c.args for c in reconcile.call_args_list]
        cli_sweep = sweep.call_args.kwargs

        self.assertEqual((uploaded, retired), (3, 0))
        self.assertEqual(tui_calls, cli_calls)  # identical set of billed rows
        self.assertEqual(tui_sweep, cli_sweep)  # identically scoped sweep


class TestReviewSessionBillsThroughSharedPath(unittest.TestCase):
    """#378 item 6: a review-derived session bills like any other session.

    Review-family sessions carry ``category='Review'`` but are otherwise ordinary
    derived windows, so they must flow through the SAME min/rounding upload path
    with no special-casing — a lone review event (zero-span) picks up the #355
    per-session minimum exactly as a lone commit would.
    """

    def test_lone_review_session_floors_to_minimum(self):
        state = make_state_db()
        state.add_event(
            EventRecord(
                id=None,
                source="review",
                timestamp=datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc),
                task_ids=["100"],
                repo="owner/repo",
                pr_num=7,
            )
        )
        # Derive through the real query path so the session dict is exactly what
        # the TUI/CLI would hand the uploader.
        query = QuerySessionsCommand(
            client=MagicMock(), state=state, config=LocalConfig.load()
        )
        sessions = query.execute(start_date="2026-06-01", end_date="2026-06-01")
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["category"], "Review")
        self.assertEqual(sessions[0]["duration_secs"], 0)  # zero-span lone event

        with patch(_RECONCILE, return_value=42) as reconcile, patch(
            _SWEEP, return_value=0
        ):
            result = upload_sessions(
                MagicMock(), state, sessions, config=LocalConfig.load()
            )
        # The review session bills the default 0.25h floor via the shared path.
        self.assertEqual(result["uploaded"], 1)
        self.assertEqual(result["rows"][0]["hours"], 0.25)
        self.assertEqual(reconcile.call_args.args[5], 0.25)


if __name__ == "__main__":
    unittest.main()
