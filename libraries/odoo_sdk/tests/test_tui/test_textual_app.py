"""Headless tests for the Textual driver (issue #605).

The whole driver — the app, its three mode screens, and every key binding — is
exercised through Textual's ``run_test`` pilot: keys are pressed exactly as a
user would press them and the assertions read the plain-text lines each panel
rendered. No live Odoo and no real terminal are involved; the injected deps are
the same in-memory fakes the transition tests use.
"""

import unittest
from datetime import date, datetime
from unittest.mock import patch

from odoo_sdk.state import EventRecord
from odoo_sdk.tui.app import AppState
from odoo_sdk.tui.textual_app import (
    MainScreen,
    OdooTuiApp,
    ReviewScreen,
    TextPanel,
    TriageScreen,
    empty_lines,
    header_text,
    stat_lines,
    timeline_lines,
)
from odoo_sdk.tui.window import DateWindow
from odoo_sdk.utilities.stats import compute_stats
from tests.test_tui._fake import (
    FakeCommand,
    FakeRegistry,
    build_fake_deps,
    build_fake_store,
    sample_sessions,
)

WINDOW = DateWindow(date(2026, 6, 1), date(2026, 6, 7))


def _writer(content, name):
    return f"/out/{name}"


def _app(deps=None, **kwargs):
    return OdooTuiApp(
        deps if deps is not None else build_fake_deps(),
        export_writer=_writer,
        initial_window=kwargs.pop("initial_window", WINDOW),
        **kwargs,
    )


def _panel_text(app, selector):
    """Return the joined plain-text lines a panel of the current screen shows."""
    return "\n".join(app.screen.query_one(selector, TextPanel).text_lines)


# ── Pure line composers ─────────────────────────────────────────────────────


class TestLineComposers(unittest.TestCase):
    def test_header_summarizes_window_and_counts(self):
        header = header_text(WINDOW, compute_stats(sample_sessions()))
        self.assertIn("odoo-tui", header)
        self.assertIn("2026-06-01 → 2026-06-07 (7d)", header)
        self.assertIn("2 sessions", header)
        self.assertIn("2 tasks", header)

    def test_stat_lines_cover_every_metric(self):
        lines = stat_lines(compute_stats(sample_sessions()))
        body = "\n".join(lines)
        for label in (
            "session hours",
            "events/day",
            "events/week",
            "peak parallel",
            "overlap ratio",
            "target util",
            "calendar util",
        ):
            self.assertIn(label, body)

    def test_empty_lines_show_hint_and_guidance(self):
        hint, guidance = empty_lines("nothing derivable here")
        self.assertEqual(hint, "nothing derivable here")
        self.assertIn("widen the window", guidance)

    def test_empty_lines_fall_back_to_placeholder(self):
        hint, _ = empty_lines("")
        self.assertEqual(hint, "(no sessions in window)")

    def test_timeline_lines_render_one_lane_per_session(self):
        lines = timeline_lines(sample_sessions(), WINDOW, 60)
        self.assertEqual(len(lines), 2)
        self.assertIn("#101", lines[0])
        self.assertIn("#202", lines[1])

    def test_timeline_lines_render_empty_state(self):
        lines = timeline_lines([], WINDOW, 60, empty_hint="custom hint")
        self.assertEqual(lines[0], "custom hint")
        self.assertIn("widen the window", lines[1])


# ── The main (timeline) screen ──────────────────────────────────────────────


class TestMainScreen(unittest.IsolatedAsyncioTestCase):
    async def test_mount_queries_and_renders_all_panels(self):
        app = _app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            self.assertIsInstance(app.screen, MainScreen)
            # The initial query ran once, bounded by the pinned window.
            call = app.deps.registry["query_sessions"].calls[0]
            self.assertEqual(call["start_date"], "2026-06-01")
            self.assertEqual(call["end_date"], "2026-06-07")
            header = _panel_text(app, "#header")
            self.assertIn("odoo-tui", header)
            self.assertIn("2 sessions", header)
            timeline = _panel_text(app, "#timeline-panel")
            self.assertIn("#101", timeline)
            self.assertIn("#202", timeline)
            self.assertIn("session hours", _panel_text(app, "#stats-panel"))

    async def test_empty_window_renders_hint_and_guidance(self):
        app = _app(build_fake_deps(query_result=[]))
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            timeline = _panel_text(app, "#timeline-panel")
            self.assertIn("no sessions derivable", timeline)
            self.assertIn("0 events in window", timeline)
            self.assertIn("gap=60m", timeline)
            self.assertIn("widen the window", timeline)

    async def test_arrow_key_moves_window_and_requeries(self):
        app = _app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("left")
            self.assertEqual(app.state.window.start, date(2026, 5, 31))
            self.assertEqual(len(app.deps.registry["query_sessions"].calls), 2)
            self.assertIn("2026-05-31", _panel_text(app, "#header"))

    async def test_all_four_window_actions_are_bound(self):
        app = _app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("left")   # start earlier
            await pilot.press("right")  # start later (back)
            await pilot.press("up")     # end later
            await pilot.press("down")   # end earlier (back)
            self.assertEqual(app.state.window, WINDOW)

    async def test_export_keys_write_through_injected_writer(self):
        app = _app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("e")
            self.assertIn(
                "exported markdown -> /out/timelog_2026-06-01_2026-06-07.md",
                _panel_text(app, "#status"),
            )
            await pilot.press("c")
            self.assertIn(
                "exported csv -> /out/timelog_2026-06-01_2026-06-07.csv",
                _panel_text(app, "#status"),
            )

    async def test_upload_gate_arms_confirms_and_uploads(self):
        app = _app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("u")
            self.assertTrue(app.state.pending_upload)
            self.assertIn("upload 2 session(s)?", _panel_text(app, "#status"))
            with patch(
                "odoo_sdk.tui.app.upload_sessions",
                return_value={"uploaded": 2, "retired": 0},
            ) as mock_upload:
                await pilot.press("y")
            self.assertFalse(app.state.pending_upload)
            self.assertIn("uploaded 2 session(s)", _panel_text(app, "#status"))
            mock_upload.assert_called_once()

    async def test_upload_gate_cancels_on_any_other_key(self):
        app = _app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("u")
            with patch("odoo_sdk.tui.app.upload_sessions") as mock_upload:
                await pilot.press("n")
            self.assertFalse(app.state.pending_upload)
            self.assertIn("upload cancelled", _panel_text(app, "#status"))
            mock_upload.assert_not_called()

    async def test_armed_gate_consumes_bound_keys_as_its_answer(self):
        # While the gate is armed a bound key (an arrow) answers the gate rather
        # than firing its binding — exactly the curses driver's gate-first order.
        app = _app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("u")
            await pilot.press("left")
            self.assertIn("upload cancelled", _panel_text(app, "#status"))
            self.assertEqual(app.state.window, WINDOW)  # the window never moved
            self.assertEqual(len(app.deps.registry["query_sessions"].calls), 1)

    async def test_resync_key_runs_command_and_reports_counts(self):
        registry = FakeRegistry(
            {
                "query_sessions": FakeCommand(result=sample_sessions()),
                "resync": FakeCommand(
                    result={"git": {"inserted": 1}, "github": {"skipped": "no gh"}}
                ),
            }
        )
        app = _app(build_fake_deps(registry=registry))
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("r")
            self.assertEqual(app.deps.registry["resync"].calls, [{}])
            status = _panel_text(app, "#status")
            self.assertIn("resync — git: +1", status)
            self.assertIn("github: skipped (no gh)", status)

    async def test_resize_rerenders_timeline(self):
        app = _app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, MainScreen)
            screen.on_resize(None)  # the handler re-renders from state
            self.assertIn("#101", _panel_text(app, "#timeline-panel"))

    async def test_quit_key_exits_the_app(self):
        app = _app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("q")
        self.assertFalse(app.is_running)


# ── The triage screen ───────────────────────────────────────────────────────


def _unattributed():
    return [
        EventRecord(
            id=1,
            source="chatter",
            timestamp=datetime(2026, 6, 1, 9, 0),
            task_ids=[],
            repo="",
            subject="Standup",
            external_id="gcal:solo-1",
        ),
        EventRecord(
            id=2,
            source="chatter",
            timestamp=datetime(2026, 6, 1, 12, 0),
            task_ids=[],
            repo="",
            subject="1:1 with Sam",
            external_id=None,
        ),
    ]


def _triage_app(assign_result={"updated": 1}):
    store = build_fake_store()
    # First read (opening triage) finds two lone events; the reload after an
    # assignment finds none left.
    store.get_unattributed_events.side_effect = [_unattributed(), []]
    registry = FakeRegistry(
        {
            "query_sessions": FakeCommand(result=sample_sessions()),
            "assign_event": FakeCommand(result=assign_result),
        }
    )
    return _app(build_fake_deps(registry=registry, store=store))


class TestTriageScreen(unittest.IsolatedAsyncioTestCase):
    async def test_t_opens_triage_and_lists_rows(self):
        app = _triage_app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("t")
            self.assertIsInstance(app.screen, TriageScreen)
            self.assertEqual(app.state.mode, "triage")
            self.assertIn(
                "2 unattributed item(s)", _panel_text(app, "#triage-header")
            )
            rows = _panel_text(app, "#triage-list")
            self.assertIn("Standup", rows)
            self.assertIn("1:1 with Sam", rows)

    async def test_digits_echo_and_backspace_edits(self):
        app = _triage_app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("t")
            await pilot.press("1", "2")
            self.assertIn("task id > 12", _panel_text(app, "#triage-input"))
            await pilot.press("backspace")
            self.assertIn("task id > 1", _panel_text(app, "#triage-input"))
            self.assertEqual(app.state.triage_input, "1")

    async def test_selection_moves_and_skip_advances(self):
        app = _triage_app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("t")
            await pilot.press("down")
            self.assertEqual(app.state.triage_selected, 1)
            await pilot.press("up")
            self.assertEqual(app.state.triage_selected, 0)
            await pilot.press("s")  # skip advances like down
            self.assertEqual(app.state.triage_selected, 1)

    async def test_enter_assigns_through_the_command(self):
        app = _triage_app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("t")
            await pilot.press("4", "2")
            await pilot.press("enter")
            call = app.deps.registry["assign_event"].calls[0]
            self.assertEqual(call, {"event_ids": [1], "task_id": 42})
            self.assertIn(
                "assigned 1 events of series gcal:solo-1 to task 42",
                _panel_text(app, "#status"),
            )
            # The assigned row dropped out of the reloaded queue.
            self.assertIn("nothing to triage", _panel_text(app, "#triage-list"))

    async def test_enter_without_task_id_reports_invalid(self):
        app = _triage_app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("t")
            await pilot.press("enter")
            self.assertIn("invalid task id", _panel_text(app, "#status"))
            self.assertEqual(app.deps.registry["assign_event"].calls, [])

    async def test_q_returns_to_the_timeline(self):
        app = _triage_app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("t")
            await pilot.press("q")
            self.assertIsInstance(app.screen, MainScreen)
            self.assertEqual(app.state.mode, "main")
            self.assertTrue(app.is_running)  # triage 'q' never quits the app


# ── The review screen ───────────────────────────────────────────────────────


class TestReviewScreen(unittest.IsolatedAsyncioTestCase):
    async def test_v_opens_review_with_one_card_per_session(self):
        app = _app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("v")
            self.assertIsInstance(app.screen, ReviewScreen)
            self.assertEqual(app.state.mode, "review")
            self.assertIn("2 session(s)", _panel_text(app, "#review-header"))
            cards = _panel_text(app, "#review-list")
            self.assertIn("task 101", cards)
            self.assertIn("task 202", cards)

    async def test_selection_moves_and_evidence_toggles(self):
        app = _app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("v")
            await pilot.press("down")
            self.assertEqual(app.state.review_selected, 1)
            await pilot.press("e")
            self.assertTrue(app.state.review_expanded)
            self.assertIn("evidence — task 202", _panel_text(app, "#review-list"))
            await pilot.press("enter")  # enter toggles the pane too
            self.assertFalse(app.state.review_expanded)
            await pilot.press("up")
            self.assertEqual(app.state.review_selected, 0)

    async def test_escape_returns_to_the_timeline(self):
        app = _app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("v")
            await pilot.press("escape")
            self.assertIsInstance(app.screen, MainScreen)
            self.assertEqual(app.state.mode, "main")
            self.assertTrue(app.is_running)  # review 'q'/esc never quits the app


# ── The apply plumbing ──────────────────────────────────────────────────────


class TestApply(unittest.IsolatedAsyncioTestCase):
    async def test_apply_stores_state_and_rerenders_current_screen(self):
        app = _app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            app.apply(
                AppState(window=WINDOW, sessions=[], status="hand-applied state")
            )
            self.assertEqual(app.state.status, "hand-applied state")
            self.assertIn("hand-applied state", _panel_text(app, "#status"))


if __name__ == "__main__":
    unittest.main()
