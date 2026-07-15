"""Tests for the TUI driver's pure command-composition and key handling.

The curses render loop itself is excluded from coverage; these tests exercise the
pure state transitions and the command composition through a fake registry, so no
terminal and no live Odoo are involved.
"""

import curses
import unittest
from datetime import date, datetime
from unittest.mock import MagicMock, patch

from odoo_sdk.tui.app import (
    AppState,
    confirm_upload,
    default_window,
    do_export,
    do_resync,
    handle_key,
    move_window,
    query_sessions,
    refresh,
    request_upload,
    run,
    _resync_status,
    _upload_sessions,
)
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


def _registry(query_result=None, store=None):
    # The TUI's ``_upload_sessions`` delegates to the shared upload loop (#354),
    # resolving its (client, state) pair off the stop_task command, so the fake
    # carries both. The state's ``list_session_uploads`` returns an empty ledger
    # so an unpatched upload's orphan sweep is a no-op (the loop's behavior is
    # tested in ``tests/test_utilities/test_upload.py``).
    stop_state = MagicMock()
    stop_state.list_session_uploads.return_value = []
    return FakeRegistry(
        {
            "query_sessions": FakeCommand(
                result=query_result or _sessions(), state=store
            ),
            "start_task": FakeCommand(result={"run_id": 1}),
            "stop_task": FakeCommand(
                result={"elapsed_hours": 1.0},
                state=stop_state,
                client=MagicMock(),
            ),
        }
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
        registry = _registry()
        window = DateWindow(date(2026, 6, 1), date(2026, 6, 3))
        query_sessions(registry, window)
        call = registry["query_sessions"].calls[0]
        self.assertEqual(call["start_date"], "2026-06-01")
        self.assertEqual(call["end_date"], "2026-06-03")
        self.assertTrue(call["include_events"])

    def test_refresh_stores_result(self):
        registry = _registry(query_result=_sessions(3))
        state = AppState(window=default_window(today=date(2026, 6, 5)), sessions=[])
        refreshed = refresh(registry, state)
        self.assertEqual(len(refreshed.sessions), 3)


class TestEmptyHint(unittest.TestCase):
    """The empty-window hint (issue #332) distinguishes no-data from no-derivable."""

    def _registry_for_hint(self, *, events, runs, gap_mins=30):
        store = MagicMock()
        store.count_events.return_value = events
        store.get_all_runs.return_value = list(range(runs))
        query = FakeCommand(result=[], state=store)
        query.config = MagicMock(session_gap_mins=gap_mins)
        return FakeRegistry({"query_sessions": query}), store

    def test_hint_only_computed_when_empty(self):
        # A populated window carries no hint, and never touches count_events.
        registry = _registry(query_result=_sessions(2))
        state = AppState(
            window=DateWindow(date(2026, 6, 1), date(2026, 6, 3)), sessions=[]
        )
        refreshed = refresh(registry, state)
        self.assertEqual(refreshed.empty_hint, "")

    def test_hint_reports_counts_and_gap(self):
        registry, _ = self._registry_for_hint(events=5, runs=3, gap_mins=45)
        state = AppState(
            window=DateWindow(date(2026, 6, 1), date(2026, 6, 3)), sessions=[]
        )
        refreshed = refresh(registry, state)
        self.assertEqual(
            refreshed.empty_hint,
            "no sessions derivable — 5 events in window, 3 runs recorded, gap=45m",
        )

    def test_hint_counts_events_over_query_bounds(self):
        # count_events is asked for [midnight start, midnight day-after-end).
        registry, store = self._registry_for_hint(events=0, runs=0)
        state = AppState(
            window=DateWindow(date(2026, 6, 1), date(2026, 6, 3)), sessions=[]
        )
        refresh(registry, state)
        lo, hi = store.count_events.call_args.args
        self.assertEqual(lo, datetime(2026, 6, 1, 0, 0, 0))
        self.assertEqual(hi, datetime(2026, 6, 4, 0, 0, 0))

    def test_no_data_case_shows_zero_events(self):
        registry, _ = self._registry_for_hint(events=0, runs=0)
        state = AppState(
            window=DateWindow(date(2026, 6, 1), date(2026, 6, 3)), sessions=[]
        )
        refreshed = refresh(registry, state)
        self.assertIn("0 events in window", refreshed.empty_hint)

    def test_data_exists_but_not_derivable_shows_nonzero_events(self):
        registry, _ = self._registry_for_hint(events=7, runs=2)
        state = AppState(
            window=DateWindow(date(2026, 6, 1), date(2026, 6, 3)), sessions=[]
        )
        refreshed = refresh(registry, state)
        self.assertIn("7 events in window", refreshed.empty_hint)

    def test_hint_cleared_when_sessions_appear_on_later_refresh(self):
        registry, store = self._registry_for_hint(events=3, runs=1)
        state = AppState(
            window=DateWindow(date(2026, 6, 1), date(2026, 6, 3)), sessions=[]
        )
        empty = refresh(registry, state)
        self.assertNotEqual(empty.empty_hint, "")
        # A later refresh that finds sessions clears the hint.
        registry["query_sessions"]._result = _sessions(2)
        populated = refresh(registry, empty)
        self.assertEqual(populated.empty_hint, "")
        self.assertEqual(len(populated.sessions), 2)


class TestMoveWindow(unittest.TestCase):
    def test_move_requeries_when_window_changes(self):
        registry = _registry()
        state = AppState(
            window=DateWindow(date(2026, 6, 3), date(2026, 6, 5)), sessions=[]
        )
        moved = move_window(registry, state, "left")
        self.assertEqual(moved.window.start, date(2026, 6, 2))
        self.assertEqual(len(registry["query_sessions"].calls), 1)

    def test_no_change_does_not_requery(self):
        registry = _registry()
        # A one-day window clamped by "right" cannot narrow further.
        state = AppState(
            window=DateWindow(date(2026, 6, 5), date(2026, 6, 5)), sessions=[]
        )
        moved = move_window(registry, state, "right")
        self.assertEqual(moved, state)
        self.assertEqual(len(registry["query_sessions"].calls), 0)


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
        registry = _registry(store=store)
        state = AppState(
            window=DateWindow(date(2026, 6, 1), date(2026, 6, 1)), sessions=[]
        )
        writer, written = self._writer()
        result = do_export(state, registry, "markdown", writer)
        self.assertIn("exported markdown", result.status)
        self.assertTrue(written["name"].endswith(".md"))

    def test_csv_export_writes_and_sets_status(self):
        store = MagicMock()
        store.get_events.return_value = []
        registry = _registry(store=store)
        state = AppState(
            window=DateWindow(date(2026, 6, 1), date(2026, 6, 1)), sessions=[]
        )
        writer, written = self._writer()
        result = do_export(state, registry, "csv", writer)
        self.assertIn("exported csv", result.status)
        self.assertTrue(written["name"].endswith(".csv"))


class TestUploadGate(unittest.TestCase):
    def test_request_upload_arms_gate(self):
        state = AppState(window=default_window(), sessions=_sessions(2))
        armed = request_upload(state)
        self.assertTrue(armed.pending_upload)
        self.assertIn("confirm", armed.status)

    def test_confirm_cancels_when_not_confirmed(self):
        registry = _registry()
        state = AppState(
            window=default_window(), sessions=_sessions(2), pending_upload=True
        )
        with patch("odoo_sdk.tui.app.upload_sessions") as mock_upload:
            resolved = confirm_upload(state, registry, confirmed=False)
        self.assertFalse(resolved.pending_upload)
        self.assertIn("cancelled", resolved.status)
        # The shared upload loop is never invoked on a cancel.
        mock_upload.assert_not_called()

    def test_confirm_runs_upload(self):
        registry = _registry()
        state = AppState(
            window=default_window(), sessions=_sessions(2), pending_upload=True
        )
        with patch(
            "odoo_sdk.tui.app.upload_sessions",
            return_value={"uploaded": 2, "retired": 0},
        ) as mock_upload:
            resolved = confirm_upload(state, registry, confirmed=True)
        self.assertFalse(resolved.pending_upload)
        self.assertIn("uploaded 2", resolved.status)
        self.assertNotIn("retired", resolved.status)  # 0 orphans stays quiet
        mock_upload.assert_called_once()

    def test_confirm_reports_retired_orphans(self):
        registry = _registry()
        state = AppState(
            window=default_window(), sessions=_sessions(1), pending_upload=True
        )
        with patch(
            "odoo_sdk.tui.app.upload_sessions",
            return_value={"uploaded": 1, "retired": 2},
        ):
            resolved = confirm_upload(state, registry, confirmed=True)
        self.assertIn("uploaded 1", resolved.status)
        self.assertIn("retired 2 orphaned upload(s)", resolved.status)


class TestUploadSessions(unittest.TestCase):
    def test_delegates_to_shared_loop_with_sessions_and_window(self):
        # The TUI upload path is a thin delegation to the shared upload loop
        # (#354): it forwards the stop_task command's (client, state) pair, the
        # derived sessions, and the window's inclusive dates (the loop resolves
        # the sweep bounds itself so the query and sweep windows cannot drift),
        # and returns the (uploaded, retired) counts for the status line.
        registry = _registry()
        window = DateWindow(date(2026, 6, 1), date(2026, 6, 3))
        sessions = _sessions(3)
        with patch(
            "odoo_sdk.tui.app.upload_sessions",
            return_value={"uploaded": 3, "retired": 1},
        ) as mock_upload:
            uploaded, retired = _upload_sessions(registry, sessions, window)
        self.assertEqual((uploaded, retired), (3, 1))
        stop_cmd = registry["stop_task"]
        mock_upload.assert_called_once_with(
            stop_cmd._client,
            stop_cmd.state,
            sessions,
            start_date="2026-06-01",
            end_date="2026-06-03",
        )


class TestHandleKey(unittest.TestCase):
    def _state(self, **kw):
        base = dict(
            window=DateWindow(date(2026, 6, 3), date(2026, 6, 5)),
            sessions=_sessions(2),
        )
        base.update(kw)
        return AppState(**base)

    def _writer(self):
        return lambda content, name: f"/out/{name}"

    def test_quit_key_returns_should_quit(self):
        registry = _registry()
        _, should_quit = handle_key(
            registry, self._state(), ord("q"), writer=self._writer()
        )
        self.assertTrue(should_quit)

    def test_escape_key_quits(self):
        registry = _registry()
        _, should_quit = handle_key(registry, self._state(), 27, writer=self._writer())
        self.assertTrue(should_quit)

    def test_arrow_key_moves_window(self):
        registry = _registry()
        state, should_quit = handle_key(
            registry, self._state(), curses.KEY_LEFT, writer=self._writer()
        )
        self.assertFalse(should_quit)
        self.assertEqual(state.window.start, date(2026, 6, 2))

    def test_upload_key_arms_gate(self):
        registry = _registry()
        state, _ = handle_key(registry, self._state(), ord("u"), writer=self._writer())
        self.assertTrue(state.pending_upload)

    def test_pending_upload_consumes_confirm_key(self):
        registry = _registry()
        state = self._state(pending_upload=True)
        resolved, should_quit = handle_key(
            registry, state, ord("y"), writer=self._writer()
        )
        self.assertFalse(should_quit)
        self.assertFalse(resolved.pending_upload)
        self.assertIn("uploaded", resolved.status)

    def test_pending_upload_cancels_on_other_key(self):
        registry = _registry()
        state = self._state(pending_upload=True)
        resolved, _ = handle_key(registry, state, ord("n"), writer=self._writer())
        self.assertIn("cancelled", resolved.status)

    def test_unknown_key_is_noop(self):
        registry = _registry()
        state = self._state()
        result, should_quit = handle_key(
            registry, state, ord("z"), writer=self._writer()
        )
        self.assertFalse(should_quit)
        self.assertEqual(result, state)

    def test_export_key_triggers_export(self):
        store = MagicMock()
        store.get_events.return_value = []
        registry = _registry(store=store)
        state = self._state(window=DateWindow(date(2026, 6, 1), date(2026, 6, 1)))
        result, _ = handle_key(registry, state, ord("e"), writer=self._writer())
        self.assertIn("exported markdown", result.status)

    def test_csv_export_key_triggers_csv_export(self):
        store = MagicMock()
        store.get_events.return_value = []
        registry = _registry(store=store)
        state = self._state(window=DateWindow(date(2026, 6, 1), date(2026, 6, 1)))
        result, _ = handle_key(registry, state, ord("c"), writer=self._writer())
        self.assertIn("exported csv", result.status)


class TestResync(unittest.TestCase):
    """The ``r`` keybind runs resync, refreshes, and reports per-source counts."""

    def _registry(self, resync_result, query_result=None):
        return FakeRegistry(
            {
                "resync": FakeCommand(result=resync_result),
                "query_sessions": FakeCommand(result=query_result or _sessions()),
            }
        )

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

    def test_do_resync_runs_command_refreshes_and_sets_status(self):
        registry = self._registry(
            {"git": {"inserted": 3}, "github": {"skipped": "no gh"}},
            query_result=_sessions(2),
        )
        state = AppState(window=default_window(today=date(2026, 6, 5)), sessions=[])
        result = do_resync(registry, state)
        # The resync command ran with the default (all) sources.
        self.assertEqual(registry["resync"].calls, [{}])
        # Sessions were re-queried and the status shows per-source counts.
        self.assertEqual(len(result.sessions), 2)
        self.assertIn("git: +3", result.status)
        self.assertIn("github: skipped (no gh)", result.status)
        self.assertFalse(result.pending_upload)

    def test_resync_key_dispatches(self):
        registry = self._registry({"git": {"inserted": 1}})
        state = AppState(
            window=DateWindow(date(2026, 6, 3), date(2026, 6, 5)), sessions=[]
        )
        result, should_quit = handle_key(
            registry, state, ord("r"), writer=lambda c, n: n
        )
        self.assertFalse(should_quit)
        self.assertIn("resync", result.status)
        self.assertEqual(len(registry["resync"].calls), 1)


class TestRunHandlesKeyboardInterrupt(unittest.TestCase):
    """``Ctrl+C`` at the blocking ``getch`` must exit cleanly (issue #125)."""

    def test_run_swallows_keyboard_interrupt(self):
        # ``curses.wrapper`` restores the terminal, then re-raises Ctrl+C as a
        # KeyboardInterrupt; ``run`` must treat it as a normal quit.
        def boom(*_args, **_kwargs):
            raise KeyboardInterrupt

        with patch("odoo_sdk.tui.app.curses.wrapper", side_effect=boom) as wrapper:
            run(_registry())  # must not raise

        wrapper.assert_called_once()

    def test_run_propagates_other_errors(self):
        # Only KeyboardInterrupt is a normal quit; real errors still surface.
        def boom(*_args, **_kwargs):
            raise RuntimeError("curses exploded")

        with patch("odoo_sdk.tui.app.curses.wrapper", side_effect=boom):
            with self.assertRaises(RuntimeError):
                run(_registry())


if __name__ == "__main__":
    unittest.main()
