"""MCP-surface tests of individual commands, moved from the core test suite.

They check the tool listing, the wire schema and the TOON-encoded output of
commands. A core test must not import an app, so they live here.
"""

import unittest
from unittest.mock import MagicMock, Mock, patch

from fastmcp.tools import Tool
from odoo_mcp.server import (
    TOON_OUTPUT_ENV,
    OdooMCPServer,
    _event_emitting,
    _to_toon,
)
from odoo_mcp.tools import build_tools

from ..support import make_event_store, make_service, wire_tools

LISTED = (
    "get_mail_status",
    "search_count",
    "unlogged_time_report",
    "schedule_activity",
    "get_activities",
    "mark_activity_done",
    "search_activity_types",
)


def _schema(name: str) -> dict:
    fn, description = build_tools(make_service(client=MagicMock()))[name]
    return Tool.from_function(fn, name=name, description=description).parameters


class TestToolListing(unittest.TestCase):
    def test_tools_are_built_with_their_command_descriptions(self):
        tools = build_tools(make_service(client=MagicMock()))
        for name in LISTED:
            self.assertIn(name, tools)
            self.assertNotEqual(tools[name][1], "")

    def test_server_registers_the_tools(self):
        added = []
        mock_mcp = MagicMock()
        mock_mcp.add_tool.side_effect = added.append
        with patch("odoo_mcp.server.FastMCP", return_value=mock_mcp):
            OdooMCPServer(make_service(client=MagicMock()))
        exposed = {tool.name for tool in added}
        for name in LISTED:
            self.assertIn(name, exposed)


class TestActivityToolSchemas(unittest.TestCase):
    """The wire schemas keep the convenience defaults reachable."""

    def test_schedule_requires_only_res_id(self):
        schema = _schema("schedule_activity")
        self.assertEqual(schema["required"], ["res_id"])
        self.assertEqual(
            schema["properties"]["res_model"].get("default"), "project.task"
        )
        for name in ("activity_type", "summary", "note", "date_deadline", "user_id"):
            self.assertIn(name, schema["properties"])

    def test_get_activities_requires_nothing(self):
        schema = _schema("get_activities")
        self.assertEqual(schema.get("required", []), [])
        self.assertEqual(schema["properties"]["limit"].get("default"), 50)

    def test_mark_done_requires_only_the_activity_id(self):
        schema = _schema("mark_activity_done")
        self.assertEqual(schema["required"], ["activity_id"])
        self.assertEqual(schema["properties"]["feedback"].get("default"), "")

    def test_search_types_requires_nothing(self):
        self.assertEqual(_schema("search_activity_types").get("required", []), [])

    def test_schedule_tool_forwards_every_argument_to_the_command(self):
        class _Service:
            registry = make_service(client=MagicMock()).registry

            def execute(self, command_id, /, **kwargs):
                return {"kwargs": kwargs}

        fn, _ = build_tools(_Service())["schedule_activity"]
        result = fn(res_id=42, activity_type="Call")
        # The command's Args dataclass fills in the defaults.
        self.assertEqual(result["kwargs"], {"res_id": 42, "activity_type": "Call"})

    def test_search_count_tool_routes_model_and_domain_to_command(self):
        captured = {}

        class _Service:
            registry = make_service(client=MagicMock()).registry

            def execute(self, name, /, **kwargs):
                captured.update(name=name, **kwargs)
                return 42

        fn, _ = build_tools(_Service())["search_count"]
        self.assertEqual(fn(model="project.task", domain=[("active", "=", True)]), 42)
        self.assertEqual(
            captured,
            {
                "name": "search_count",
                "model": "project.task",
                "domain": [("active", "=", True)],
            },
        )


class TestToonEncoding(unittest.TestCase):
    """Command results encode cleanly under the TOON output flag."""

    def _encode(self, result):
        with patch.dict("os.environ", {TOON_OUTPUT_ENV: "1"}):
            out = _to_toon(result)
        self.assertIsInstance(out, str)
        return out

    def test_list_of_dicts_results_encode(self):
        out = self._encode(
            [{"model": "project.task", "state": "exception", "body": "needle"}]
        )
        for text in ("project.task", "exception", "needle"):
            self.assertIn(text, out)

    def test_dict_results_encode(self):
        out = self._encode({"name": "report.csv", "total_hours": 1.5})
        self.assertIn("report.csv", out)
        self.assertIn("total_hours", out)

    def test_timesheet_summary_full_path_encodes_as_toon(self):
        client = MagicMock()
        client.execute.return_value = [
            {"project_id": [5, "Accounting"], "unit_amount": 8.0, "__count": 3},
        ]
        tool = build_tools(make_service(client=client))["timesheet_summary"][0]
        tools = wire_tools(make_service(client=client), {"timesheet_summary": tool})
        with patch.dict("os.environ", {TOON_OUTPUT_ENV: "1"}):
            result = tools["timesheet_summary"].fn(
                start_date="2026-07-01",
                end_date="2026-07-31",
                group_by="project",
                only_mine=False,
            )
        self.assertIsInstance(result, str)
        for text in ("group_by", "total_hours", "Accounting"):
            self.assertIn(text, result)


class TestEventWrapperDispatchedRuns(unittest.TestCase):
    def test_mcp_wrapper_emits_for_interactive_and_not_for_dispatched(self):
        db = make_event_store()
        interactive = _event_emitting(
            lambda task_id: {"task_id": task_id},
            "t",
            make_service(db, Mock(), env={}),
        )
        interactive(task_id=5)
        self.assertEqual(len(db.get_events()), 1)
        dispatched = _event_emitting(
            lambda task_id: {"task_id": task_id},
            "t",
            make_service(db, Mock(), env={"ODOO_UNATTENDED": "1"}),
        )
        dispatched(task_id=5)
        self.assertEqual(len(db.get_events()), 1)


if __name__ == "__main__":
    unittest.main()
