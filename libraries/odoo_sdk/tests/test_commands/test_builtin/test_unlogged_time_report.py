"""Tests for the read-only ``unlogged_time_report`` builtin and its MCP tool.

The command delegates to
:func:`odoo_sdk.utilities.unlogged_time.unlogged_time_report`, which composes the
existing session derivation and dry-run billing path with a read-only Odoo
``read_group`` over ``account.analytic.line``. Every test drives real derived
sessions from a schema-provisioned state DB against a mocked Odoo transport — no
network, no writes.
"""

import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from odoo_sdk.commands.builtin import BUILTIN_COMMANDS, UnloggedTimeReportCommand
from odoo_sdk.commands.command_registry import Registry
from odoo_sdk.mcp.tools import build_explicit_tools
from odoo_sdk.mcp.tools.atomic import make_unlogged_time_report_tool
from odoo_sdk.state import EventRecord, LocalConfig
from odoo_sdk.transport.errors import OdooTransportError
from tests.support import make_state_db

UTC = timezone.utc
_HELPER = (
    "odoo_sdk.commands.builtin.unlogged_time_report.unlogged_time_report"
)


def _config() -> LocalConfig:
    # 60-minute gap keeps hourly-chained commits in one session; the billing
    # policy (0.25 floor, 0.05 rounding) is the resolved default.
    return LocalConfig(behavior={"session_gap_mins": 60})


def _commit(state, hour, minute=0, day=1, task="101"):
    return state.add_event(
        EventRecord(
            id=None,
            source="commit",
            timestamp=datetime(2026, 7, day, hour, minute, tzinfo=UTC),
            task_ids=[task],
            repo="o/r",
        )
    )


def _logged_row(task_id, name, day_iso, hours):
    """Shape one two-axis ``read_group`` row (task_id x date:day)."""
    return {
        "task_id": [task_id, name],
        "date:day": day_iso,
        "unit_amount": hours,
        "__count": 1,
        "__range": {"date:day": {"from": day_iso, "to": day_iso}},
    }


class _FakeClient:
    """Minimal Odoo transport: routes the two read-only calls the report makes."""

    def __init__(self, logged_rows, *, uid=42, employee_id=7, fail=False):
        self.uid = uid
        self._logged_rows = logged_rows
        self._employee_id = employee_id
        self._fail = fail
        self.calls = []

    def execute(self, model, method, *args, **kwargs):
        self.calls.append((model, method))
        if self._fail:
            raise OdooTransportError("connection refused")
        if model == "hr.employee" and method == "search_read":
            return [{"id": self._employee_id}]
        if model == "account.analytic.line" and method == "read_group":
            return self._logged_rows
        raise AssertionError(f"unexpected call {model}.{method}")


def _state_with_sessions():
    """Provision a state DB with three derived sessions across two days.

    * task 101 (July 1): commits 09:00-12:00 chained -> a 3.0h session.
    * task 202 (July 1): commits 09:00, 09:30 -> a 0.5h session.
    * task 303 (July 2): one commit -> a single-event session (0.0h wall).
    """
    state = make_state_db()
    for hour in (9, 10, 11, 12):
        _commit(state, hour, task="101")
    _commit(state, 9, minute=0, task="202")
    _commit(state, 9, minute=30, task="202")
    _commit(state, 14, day=2, task="303")
    return state


def _run(state, client, **kwargs):
    return UnloggedTimeReportCommand(
        client, state=state, config=_config()
    ).execute("2026-07-01", "2026-07-02", **kwargs)


class TestUnloggedTimeReport(unittest.TestCase):
    def test_registered_under_its_name(self):
        self.assertIs(
            BUILTIN_COMMANDS["unlogged_time_report"], UnloggedTimeReportCommand
        )
        self.assertEqual(
            UnloggedTimeReportCommand(MagicMock()).name, "unlogged_time_report"
        )

    def test_nonzero_delta_rows_only_by_default(self):
        state = _state_with_sessions()
        client = _FakeClient(
            [
                _logged_row(101, "Task A", "2026-07-01", 1.0),
                _logged_row(202, "Task B", "2026-07-01", 0.5),
            ]
        )
        report = _run(state, client)

        days = {day["day"]: day for day in report["days"]}
        # July 1: task 101 derived 3.0 vs logged 1.0 -> +2.0 shown; the fully
        # logged task 202 (0.5 == 0.5) is filtered out by default.
        july1 = days["2026-07-01"]
        self.assertEqual(len(july1["rows"]), 1)
        row = july1["rows"][0]
        self.assertEqual(row["task_id"], 101)
        self.assertEqual(row["task"], "Task A")
        self.assertEqual(row["derived_hours"], 3.0)
        self.assertEqual(row["logged_hours"], 1.0)
        self.assertEqual(row["delta"], 2.0)
        # Per-day totals cover every cell (the reconciled task 202 included).
        self.assertEqual(july1["derived_hours"], 3.5)
        self.assertEqual(july1["logged_hours"], 1.5)
        self.assertEqual(july1["delta"], 2.0)

    def test_single_event_session_bills_the_minimum(self):
        state = _state_with_sessions()
        client = _FakeClient([_logged_row(101, "Task A", "2026-07-01", 1.0)])
        report = _run(state, client)

        days = {day["day"]: day for day in report["days"]}
        # The single-event task 303 session spans 0.0h wall but the billing
        # transform floors it up to the 0.25 minimum -> a +0.25 delta.
        july2 = days["2026-07-02"]
        self.assertEqual(len(july2["rows"]), 1)
        self.assertEqual(july2["rows"][0]["task_id"], 303)
        self.assertEqual(july2["rows"][0]["derived_hours"], 0.25)
        self.assertEqual(july2["rows"][0]["delta"], 0.25)

    def test_window_totals_cover_all_cells(self):
        state = _state_with_sessions()
        client = _FakeClient(
            [
                _logged_row(101, "Task A", "2026-07-01", 1.0),
                _logged_row(202, "Task B", "2026-07-01", 0.5),
            ]
        )
        report = _run(state, client)
        # derived 3.0 + 0.5 + 0.25 = 3.75; logged 1.0 + 0.5 = 1.5.
        self.assertEqual(report["total_derived_hours"], 3.75)
        self.assertEqual(report["total_logged_hours"], 1.5)
        self.assertEqual(report["total_delta_hours"], 2.25)

    def test_include_all_keeps_zero_delta_rows(self):
        state = _state_with_sessions()
        client = _FakeClient(
            [
                _logged_row(101, "Task A", "2026-07-01", 1.0),
                _logged_row(202, "Task B", "2026-07-01", 0.5),
            ]
        )
        report = _run(state, client, include_all=True)

        days = {day["day"]: day for day in report["days"]}
        july1_tasks = {row["task_id"]: row for row in days["2026-07-01"]["rows"]}
        self.assertIn(202, july1_tasks)  # reconciled row now surfaces
        self.assertEqual(july1_tasks[202]["delta"], 0.0)
        self.assertEqual(july1_tasks[202]["derived_hours"], 0.5)

    def test_empty_window_is_an_empty_report_not_an_error(self):
        state = make_state_db()  # no events
        client = _FakeClient([])
        report = _run(state, client)
        self.assertEqual(report["days"], [])
        self.assertEqual(report["total_delta_hours"], 0.0)
        self.assertEqual(report["total_derived_hours"], 0.0)

    def test_only_mine_false_skips_the_employee_lookup(self):
        state = make_state_db()
        client = _FakeClient([])
        _run(state, client, only_mine=False)
        self.assertNotIn(("hr.employee", "search_read"), client.calls)
        self.assertIn(("account.analytic.line", "read_group"), client.calls)

    def test_only_mine_true_scopes_to_the_employee(self):
        state = make_state_db()
        client = _FakeClient([])
        _run(state, client, only_mine=True)
        self.assertIn(("hr.employee", "search_read"), client.calls)

    def test_odoo_unreachable_raises_one_clear_error(self):
        state = _state_with_sessions()
        client = _FakeClient([], fail=True)
        with self.assertRaises(OdooTransportError) as ctx:
            _run(state, client)
        self.assertEqual(
            str(ctx.exception),
            "unlogged_time_report needs a reachable Odoo instance to read "
            "logged timesheets.",
        )

    def test_read_group_failure_is_the_clear_error(self):
        # only_mine=False skips the employee lookup, so the failure must come
        # from the read_group itself and still map to the one clear message.
        state = _state_with_sessions()
        client = _FakeClient([], fail=True)
        with self.assertRaises(OdooTransportError) as ctx:
            _run(state, client, only_mine=False)
        self.assertEqual(
            str(ctx.exception),
            "unlogged_time_report needs a reachable Odoo instance to read "
            "logged timesheets.",
        )
        self.assertIn(("account.analytic.line", "read_group"), client.calls)

    def test_taskless_logged_lines_are_ignored(self):
        state = make_state_db()  # no derived sessions
        client = _FakeClient(
            [
                {
                    "task_id": False,  # a timesheet line not tied to a task
                    "date:day": "2026-07-01",
                    "unit_amount": 4.0,
                    "__count": 1,
                    "__range": {"date:day": {"from": "2026-07-01", "to": "2026-07-02"}},
                }
            ]
        )
        report = _run(state, client)
        self.assertEqual(report["days"], [])
        self.assertEqual(report["total_logged_hours"], 0.0)

    def test_session_starting_before_window_is_excluded(self):
        state = make_state_db()
        # A session whose commits begin June 30 (start day outside the window)
        # but which overlaps the window is dropped from the day-bucketed report.
        state.add_event(
            EventRecord(
                id=None,
                source="commit",
                timestamp=datetime(2026, 6, 30, 23, 30, tzinfo=UTC),
                task_ids=["909"],
                repo="o/r",
            )
        )
        state.add_event(
            EventRecord(
                id=None,
                source="commit",
                timestamp=datetime(2026, 7, 1, 0, 0, tzinfo=UTC),
                task_ids=["909"],
                repo="o/r",
            )
        )
        client = _FakeClient([])
        report = _run(state, client)
        self.assertEqual(report["days"], [])
        self.assertEqual(report["total_derived_hours"], 0.0)

    def test_fully_reconciled_day_is_omitted(self):
        state = make_state_db()
        _commit(state, 9, minute=0, day=2, task="202")
        _commit(state, 9, minute=30, day=2, task="202")  # a 0.5h session
        client = _FakeClient([_logged_row(202, "Task B", "2026-07-02", 0.5)])
        report = _run(state, client)
        # The only day is fully logged (delta 0) -> no day survives the filter.
        self.assertEqual(report["days"], [])
        self.assertEqual(report["total_delta_hours"], 0.0)
        self.assertEqual(report["total_derived_hours"], 0.5)

    def test_command_delegates_to_helper_with_all_arguments(self):
        client = MagicMock()
        cmd = UnloggedTimeReportCommand(client, state=MagicMock(), config=MagicMock())
        with patch(_HELPER, return_value={"ok": True}) as helper:
            result = cmd.execute(
                "2026-07-01", "2026-07-15", only_mine=False, include_all=True
            )
        self.assertEqual(result, {"ok": True})
        self.assertEqual(helper.call_args.args[3:], ("2026-07-01", "2026-07-15"))
        self.assertEqual(
            helper.call_args.kwargs, {"only_mine": False, "include_all": True}
        )


class TestUnloggedTimeReportMcpTool(unittest.TestCase):
    def test_registered_in_the_explicit_tool_surface(self):
        registry = Registry(MagicMock())
        for name, command in BUILTIN_COMMANDS.items():
            registry.register(name, command)
        tools = build_explicit_tools(registry)
        self.assertIn("unlogged_time_report", tools)

    def test_tool_routes_to_the_command(self):
        state = _state_with_sessions()
        client = _FakeClient(
            [
                _logged_row(101, "Task A", "2026-07-01", 1.0),
                _logged_row(202, "Task B", "2026-07-01", 0.5),
            ]
        )

        class _Reg:
            def __getitem__(self, name):
                return UnloggedTimeReportCommand(
                    client, state=state, config=_config()
                )

        tool = make_unlogged_time_report_tool(_Reg())
        report = tool("2026-07-01", "2026-07-02")
        self.assertEqual(report["start_date"], "2026-07-01")
        self.assertEqual(report["total_delta_hours"], 2.25)


if __name__ == "__main__":
    unittest.main()
