"""Tests for the TUI's pure command-composition and state transitions.

The Textual driver itself is exercised headlessly in ``test_textual_app``; these
tests cover the pure transitions and the command composition through a fake
registry, so no terminal and no live Odoo are involved. The transitions receive
their dependencies as an injected :class:`~odoo_sdk.tui.app.TuiDeps` bundle
(client, store, config, and the command registry) rather than harvesting them
off command instances.
"""

import unittest
from datetime import date, datetime
from unittest.mock import MagicMock, patch

from odoo_sdk.tui.app import (
    AppState,
    TuiDeps,
    confirm_upload,
    default_window,
    do_export,
    do_resync,
    erase_triage_digit,
    exit_review,
    exit_triage,
    move_review_selection,
    move_triage_selection,
    move_window,
    query_sessions,
    refresh,
    request_upload,
    run,
    toggle_evidence,
    type_triage_digit,
    _resync_status,
    _upload_sessions,
)
from odoo_sdk.transport.errors import OdooServerError
from odoo_sdk.tui.evidence import WEAK, ReviewCard
from odoo_sdk.tui.triage import TriageRow
from odoo_sdk.tui.window import DateWindow


def _sessions(n=2):
    return [
        {
            "session_id": i,
            "session_key": f"{100 + i}|{i}",
            "task_id": str(100 + i),
            "repo": "acme/web",
            "strategy_name": "development",
            "started_at": "2026-06-01T09:00:00",
            "ended_at": "2026-06-01T10:00:00",
            "duration_secs": 3600,
            "events": [],
        }
        for i in range(n)
    ]


class FakeCommand:
    """A recording stand-in for a registry command."""

    def __init__(self, result=None, state=None, client=None):
        self._result = result
        self.state = state
        self._client = client
        self.calls = []

    def execute(self, **kwargs):
        self.calls.append(kwargs)
        return self._result


class FakeRegistry:
    """A dict-backed registry returning pre-seeded fake commands."""

    def __init__(self, commands):
        self._commands = commands

    def __getitem__(self, name):
        return self._commands[name]


def _registry(query_result=None):
    return FakeRegistry(
        {
            "query_sessions": FakeCommand(result=query_result or _sessions()),
            "start_task": FakeCommand(result={"run_id": 1}),
            "stop_task": FakeCommand(result={"elapsed_hours": 1.0}),
        }
    )


def _default_store():
    # The shared upload loop's orphan sweep reads the ledger; an empty one keeps
    # an unpatched upload a no-op (the loop is tested in test_utilities/test_upload).
    store = MagicMock()
    store.list_session_uploads.return_value = []
    return store


def _deps(query_result=None, store=None, client=None, config=None, registry=None):
    """Build a :class:`TuiDeps` over fakes for the driver's injected dependencies."""
    if registry is None:
        registry = _registry(query_result=query_result)
    return TuiDeps(
        registry=registry,
        client=client if client is not None else MagicMock(),
        store=store if store is not None else _default_store(),
        config=config if config is not None else MagicMock(),
    )


class TestDefaultWindow(unittest.TestCase):
    def test_spans_requested_days_ending_today(self):
        window = default_window(today=date(2026, 6, 10), span_days=7)
        self.assertEqual(window.end, date(2026, 6, 10))
        self.assertEqual(window.start, date(2026, 6, 4))
        self.assertEqual(window.days, 7)

    def test_single_day_span(self):
        window = default_window(today=date(2026, 6, 10), span_days=1)
        self.assertEqual(window.start, window.end)


class TestQueryAndRefresh(unittest.TestCase):
    def test_query_sessions_passes_window_bounds(self):
        deps = _deps()
        window = DateWindow(date(2026, 6, 1), date(2026, 6, 3))
        query_sessions(deps, window)
        call = deps.registry["query_sessions"].calls[0]
        self.assertEqual(call["start_date"], "2026-06-01")
        self.assertEqual(call["end_date"], "2026-06-03")
        self.assertTrue(call["include_events"])

    def test_refresh_stores_result(self):
        deps = _deps(query_result=_sessions(3))
        state = AppState(window=default_window(today=date(2026, 6, 5)), sessions=[])
        refreshed = refresh(deps, state)
        self.assertEqual(len(refreshed.sessions), 3)


class TestEmptyHint(unittest.TestCase):
    """The empty-window hint (issue #332) distinguishes no-data from no-derivable."""

    def _deps_for_hint(self, *, events, runs, gap_mins=30):
        store = MagicMock()
        store.count_events.return_value = events
        store.get_all_runs.return_value = list(range(runs))
        registry = FakeRegistry({"query_sessions": FakeCommand(result=[])})
        deps = _deps(
            registry=registry,
            store=store,
            config=MagicMock(session_gap_mins=gap_mins),
        )
        return deps, store

    def test_hint_only_computed_when_empty(self):
        # A populated window carries no hint, and never touches count_events.
        deps = _deps(query_result=_sessions(2))
        state = AppState(
            window=DateWindow(date(2026, 6, 1), date(2026, 6, 3)), sessions=[]
        )
        refreshed = refresh(deps, state)
        self.assertEqual(refreshed.empty_hint, "")

    def test_hint_reports_counts_and_gap(self):
        deps, _ = self._deps_for_hint(events=5, runs=3, gap_mins=45)
        state = AppState(
            window=DateWindow(date(2026, 6, 1), date(2026, 6, 3)), sessions=[]
        )
        refreshed = refresh(deps, state)
        self.assertEqual(
            refreshed.empty_hint,
            "no sessions derivable — 5 events in window, 3 runs recorded, gap=45m",
        )

    def test_hint_counts_events_over_query_bounds(self):
        # count_events is asked for [midnight start, midnight day-after-end).
        deps, store = self._deps_for_hint(events=0, runs=0)
        state = AppState(
            window=DateWindow(date(2026, 6, 1), date(2026, 6, 3)), sessions=[]
        )
        refresh(deps, state)
        lo, hi = store.count_events.call_args.args
        self.assertEqual(lo, datetime(2026, 6, 1, 0, 0, 0))
        self.assertEqual(hi, datetime(2026, 6, 4, 0, 0, 0))

    def test_no_data_case_shows_zero_events(self):
        deps, _ = self._deps_for_hint(events=0, runs=0)
        state = AppState(
            window=DateWindow(date(2026, 6, 1), date(2026, 6, 3)), sessions=[]
        )
        refreshed = refresh(deps, state)
        self.assertIn("0 events in window", refreshed.empty_hint)

    def test_data_exists_but_not_derivable_shows_nonzero_events(self):
        deps, _ = self._deps_for_hint(events=7, runs=2)
        state = AppState(
            window=DateWindow(date(2026, 6, 1), date(2026, 6, 3)), sessions=[]
        )
        refreshed = refresh(deps, state)
        self.assertIn("7 events in window", refreshed.empty_hint)

    def test_hint_cleared_when_sessions_appear_on_later_refresh(self):
        deps, store = self._deps_for_hint(events=3, runs=1)
        state = AppState(
            window=DateWindow(date(2026, 6, 1), date(2026, 6, 3)), sessions=[]
        )
        empty = refresh(deps, state)
        self.assertNotEqual(empty.empty_hint, "")
        # A later refresh that finds sessions clears the hint.
        deps.registry["query_sessions"]._result = _sessions(2)
        populated = refresh(deps, empty)
        self.assertEqual(populated.empty_hint, "")
        self.assertEqual(len(populated.sessions), 2)


class TestMoveWindow(unittest.TestCase):
    def test_move_requeries_when_window_changes(self):
        deps = _deps()
        state = AppState(
            window=DateWindow(date(2026, 6, 3), date(2026, 6, 5)), sessions=[]
        )
        moved = move_window(deps, state, "left")
        self.assertEqual(moved.window.start, date(2026, 6, 2))
        self.assertEqual(len(deps.registry["query_sessions"].calls), 1)

    def test_no_change_does_not_requery(self):
        deps = _deps()
        # A one-day window clamped by "right" cannot narrow further.
        state = AppState(
            window=DateWindow(date(2026, 6, 5), date(2026, 6, 5)), sessions=[]
        )
        moved = move_window(deps, state, "right")
        self.assertEqual(moved, state)
        self.assertEqual(len(deps.registry["query_sessions"].calls), 0)


class TestExport(unittest.TestCase):
    def _writer(self):
        written = {}

        def writer(content, name):
            written["content"] = content
            written["name"] = name
            return f"/out/{name}"

        return writer, written

    def test_markdown_export_writes_and_sets_status(self):
        store = MagicMock()
        store.get_events.return_value = []
        deps = _deps(store=store)
        state = AppState(
            window=DateWindow(date(2026, 6, 1), date(2026, 6, 1)), sessions=[]
        )
        writer, written = self._writer()
        result = do_export(state, deps, "markdown", writer)
        self.assertIn("exported markdown", result.status)
        self.assertTrue(written["name"].endswith(".md"))

    def test_csv_export_writes_and_sets_status(self):
        store = MagicMock()
        store.get_events.return_value = []
        deps = _deps(store=store)
        state = AppState(
            window=DateWindow(date(2026, 6, 1), date(2026, 6, 1)), sessions=[]
        )
        writer, written = self._writer()
        result = do_export(state, deps, "csv", writer)
        self.assertIn("exported csv", result.status)
        self.assertTrue(written["name"].endswith(".csv"))


class TestUploadGate(unittest.TestCase):
    def test_request_upload_arms_gate(self):
        state = AppState(window=default_window(), sessions=_sessions(2))
        armed = request_upload(state)
        self.assertTrue(armed.pending_upload)
        self.assertIn("confirm", armed.status)

    def test_confirm_cancels_when_not_confirmed(self):
        deps = _deps()
        state = AppState(
            window=default_window(), sessions=_sessions(2), pending_upload=True
        )
        with patch("odoo_sdk.tui.app.upload_sessions") as mock_upload:
            resolved = confirm_upload(state, deps, confirmed=False)
        self.assertFalse(resolved.pending_upload)
        self.assertIn("cancelled", resolved.status)
        # The shared upload loop is never invoked on a cancel.
        mock_upload.assert_not_called()

    def test_confirm_runs_upload(self):
        deps = _deps()
        state = AppState(
            window=default_window(), sessions=_sessions(2), pending_upload=True
        )
        with patch(
            "odoo_sdk.tui.app.upload_sessions",
            return_value={"uploaded": 2, "retired": 0},
        ) as mock_upload:
            resolved = confirm_upload(state, deps, confirmed=True)
        self.assertFalse(resolved.pending_upload)
        self.assertIn("uploaded 2", resolved.status)
        self.assertNotIn("retired", resolved.status)  # 0 orphans stays quiet
        mock_upload.assert_called_once()

    def test_confirm_reports_retired_orphans(self):
        deps = _deps()
        state = AppState(
            window=default_window(), sessions=_sessions(1), pending_upload=True
        )
        with patch(
            "odoo_sdk.tui.app.upload_sessions",
            return_value={"uploaded": 1, "retired": 2},
        ):
            resolved = confirm_upload(state, deps, confirmed=True)
        self.assertIn("uploaded 1", resolved.status)
        self.assertIn("retired 2 orphaned upload(s)", resolved.status)

    def test_confirm_surfaces_partial_failures(self):
        # #582/#576: a session that faulted server-side is isolated by the shared
        # loop into a ``failed`` row; the TUI surfaces it (count + reason) next to
        # the billed count so a partial upload is never silent.
        deps = _deps()
        state = AppState(
            window=default_window(), sessions=_sessions(3), pending_upload=True
        )
        with patch(
            "odoo_sdk.tui.app.upload_sessions",
            return_value={
                "uploaded": 2,
                "retired": 0,
                "failed": [
                    {
                        "session_key": "101|1",
                        "task_id": 101,
                        "error": "At least one analytic account must be set",
                    }
                ],
            },
        ):
            resolved = confirm_upload(state, deps, confirmed=True)
        self.assertFalse(resolved.pending_upload)
        self.assertIn("uploaded 2", resolved.status)
        self.assertIn("1 failed", resolved.status)
        self.assertIn("analytic account", resolved.status)

    def test_confirm_summarises_multiple_failures_with_first_reason(self):
        deps = _deps()
        state = AppState(
            window=default_window(), sessions=_sessions(3), pending_upload=True
        )
        with patch(
            "odoo_sdk.tui.app.upload_sessions",
            return_value={
                "uploaded": 1,
                "retired": 0,
                "failed": [
                    {"session_key": "101|1", "error": "boom"},
                    {"session_key": "102|2", "error": "later"},
                ],
            },
        ):
            resolved = confirm_upload(state, deps, confirmed=True)
        self.assertIn("2 failed (first: boom)", resolved.status)

    def test_confirm_catches_upload_error_without_crashing(self):
        # #576: a fault that escapes the loop (e.g. the connection dropping) must
        # be caught and rendered on the status line, never propagate out of the
        # driver and kill the app.
        deps = _deps()
        state = AppState(
            window=default_window(), sessions=_sessions(1), pending_upload=True
        )
        with patch(
            "odoo_sdk.tui.app.upload_sessions",
            side_effect=OdooServerError("connection lost"),
        ):
            resolved = confirm_upload(state, deps, confirmed=True)  # must not raise
        self.assertFalse(resolved.pending_upload)
        self.assertIn("upload failed", resolved.status)
        self.assertIn("connection lost", resolved.status)


class TestUploadSessions(unittest.TestCase):
    def test_delegates_to_shared_loop_with_sessions_and_window(self):
        # The TUI upload path is a thin delegation to the shared upload loop
        # (#354): it forwards the driver's own injected (client, store) pair, the
        # derived sessions, and the window's inclusive dates (the loop resolves
        # the sweep bounds itself so the query and sweep windows cannot drift),
        # and returns the (uploaded, retired) counts for the status line.
        deps = _deps()
        window = DateWindow(date(2026, 6, 1), date(2026, 6, 3))
        sessions = _sessions(3)
        with patch(
            "odoo_sdk.tui.app.upload_sessions",
            return_value={"uploaded": 3, "retired": 1},
        ) as mock_upload:
            uploaded, retired = _upload_sessions(deps, sessions, window)
        self.assertEqual((uploaded, retired), (3, 1))
        mock_upload.assert_called_once_with(
            deps.client,
            deps.store,
            sessions,
            start_date="2026-06-01",
            end_date="2026-06-03",
        )


class TestResync(unittest.TestCase):
    """The ``r`` keybind runs resync, refreshes, and reports per-source counts."""

    def _resync_deps(self, resync_result, query_result=None):
        registry = FakeRegistry(
            {
                "resync": FakeCommand(result=resync_result),
                "query_sessions": FakeCommand(result=query_result or _sessions()),
            }
        )
        return _deps(registry=registry)

    def test_resync_status_summarizes_inserts_and_skips(self):
        status = _resync_status(
            {
                "git": {"inserted": 2},
                "github": {"skipped": "no gh"},
                "odoo": {"inserted": 0},
            }
        )
        self.assertEqual(
            status, "resync — git: +2, github: skipped (no gh), odoo: +0"
        )

    def test_resync_status_handles_empty(self):
        self.assertEqual(_resync_status({}), "resync — nothing to do")

    def test_resync_status_renders_errors_and_unattributed_warning(self):
        # #652: an entirely-unusable source renders as an error, not a skip;
        # #653: reviews that resolved no task id are surfaced, never silent.
        status = _resync_status(
            {
                "git": {"error": "no git repositories under /tmp/x"},
                "github": {
                    "inserted": 2,
                    "found": 4,
                    "unattributed_reviews": ["o/r#5"],
                },
            }
        )
        self.assertIn("git: error (no git repositories under /tmp/x)", status)
        self.assertIn("github: +2 (1 review(s) without task id)", status)

    def test_do_resync_runs_command_refreshes_and_sets_status(self):
        deps = self._resync_deps(
            {"git": {"inserted": 3}, "github": {"skipped": "no gh"}},
            query_result=_sessions(2),
        )
        state = AppState(window=default_window(today=date(2026, 6, 5)), sessions=[])
        result = do_resync(deps, state)
        # The resync command ran with the default (all) sources.
        self.assertEqual(deps.registry["resync"].calls, [{}])
        # Sessions were re-queried and the status shows per-source counts.
        self.assertEqual(len(result.sessions), 2)
        self.assertIn("git: +3", result.status)
        self.assertIn("github: skipped (no gh)", result.status)
        self.assertFalse(result.pending_upload)


def _triage_state(rows, **kw):
    base = dict(
        window=DateWindow(date(2026, 6, 1), date(2026, 6, 3)),
        sessions=[],
        mode="triage",
        triage_rows=rows,
    )
    base.update(kw)
    return AppState(**base)


def _triage_rows(n=3):
    return [
        TriageRow(f"gcal:evt-{i}", (i,), "chatter", "2026-06-01T09:00:00", f"m{i}")
        for i in range(n)
    ]


class TestTriageTransitions(unittest.TestCase):
    """The pure triage-mode transitions the Textual bindings dispatch to."""

    def test_type_digit_appends(self):
        state = _triage_state(_triage_rows(), triage_input="24")
        self.assertEqual(type_triage_digit(state, "6").triage_input, "246")

    def test_erase_digit_trims_and_is_safe_on_empty(self):
        state = _triage_state(_triage_rows(), triage_input="246")
        self.assertEqual(erase_triage_digit(state).triage_input, "24")
        empty = _triage_state(_triage_rows(), triage_input="")
        self.assertEqual(erase_triage_digit(empty).triage_input, "")

    def test_move_selection_clamps_and_discards_input(self):
        state = _triage_state(_triage_rows(3), triage_selected=0, triage_input="9")
        moved = move_triage_selection(state, 1)
        self.assertEqual(moved.triage_selected, 1)
        self.assertEqual(moved.triage_input, "")
        self.assertEqual(move_triage_selection(moved, -5).triage_selected, 0)
        self.assertEqual(move_triage_selection(moved, 5).triage_selected, 2)

    def test_move_selection_on_empty_queue_is_noop(self):
        state = _triage_state([])
        self.assertEqual(move_triage_selection(state, 1), state)

    def test_exit_returns_to_main_and_clears_input(self):
        state = _triage_state(_triage_rows(), triage_input="12", status="x")
        left = exit_triage(state)
        self.assertEqual(left.mode, "main")
        self.assertEqual(left.triage_input, "")
        self.assertEqual(left.status, "")


def _review_state(cards, **kw):
    base = dict(
        window=DateWindow(date(2026, 6, 1), date(2026, 6, 3)),
        sessions=[],
        mode="review",
        review_cards=cards,
    )
    base.update(kw)
    return AppState(**base)


def _review_cards(n=3):
    return [
        ReviewCard(
            i, str(100 + i), "2026-06-01T09:00:00", "2026-06-01T10:00:00",
            1.0, WEAK, 0.0, "", (), (), False,
        )
        for i in range(n)
    ]


class TestReviewTransitions(unittest.TestCase):
    """The pure review-mode transitions the Textual bindings dispatch to."""

    def test_move_selection_clamps_and_collapses_pane(self):
        state = _review_state(_review_cards(3), review_expanded=True)
        moved = move_review_selection(state, 1)
        self.assertEqual(moved.review_selected, 1)
        self.assertFalse(moved.review_expanded)
        self.assertEqual(move_review_selection(moved, -5).review_selected, 0)
        self.assertEqual(move_review_selection(moved, 5).review_selected, 2)

    def test_move_selection_on_empty_cards_is_noop(self):
        state = _review_state([])
        self.assertEqual(move_review_selection(state, 1), state)

    def test_toggle_evidence_flips_and_guards_empty(self):
        state = _review_state(_review_cards())
        opened = toggle_evidence(state)
        self.assertTrue(opened.review_expanded)
        self.assertFalse(toggle_evidence(opened).review_expanded)
        empty = _review_state([])
        self.assertEqual(toggle_evidence(empty), empty)

    def test_exit_returns_to_main_and_collapses(self):
        state = _review_state(_review_cards(), review_expanded=True, status="x")
        left = exit_review(state)
        self.assertEqual(left.mode, "main")
        self.assertFalse(left.review_expanded)
        self.assertEqual(left.status, "")


class TestRunHandlesKeyboardInterrupt(unittest.TestCase):
    """``Ctrl+C`` in the Textual loop must exit cleanly (issue #125)."""

    def test_run_swallows_keyboard_interrupt(self):
        # Textual restores the terminal on shutdown; ``run`` must treat a Ctrl+C
        # surfacing as KeyboardInterrupt as a normal quit.
        with patch("odoo_sdk.tui.textual_app.OdooTuiApp") as MockApp:
            MockApp.return_value.run.side_effect = KeyboardInterrupt
            run(_deps())  # must not raise
        MockApp.assert_called_once()
        MockApp.return_value.run.assert_called_once_with()

    def test_run_propagates_other_errors(self):
        # Only KeyboardInterrupt is a normal quit; real errors still surface.
        with patch("odoo_sdk.tui.textual_app.OdooTuiApp") as MockApp:
            MockApp.return_value.run.side_effect = RuntimeError("driver exploded")
            with self.assertRaises(RuntimeError):
                run(_deps())


if __name__ == "__main__":
    unittest.main()
