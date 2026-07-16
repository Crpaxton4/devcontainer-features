"""Tests for the ``timesheet_summary`` built-in command and its MCP tool.

The command is a thin delegator to ``billing.timesheet_reports.timesheet_summary``;
the TOON case drives the full atomic tool -> command -> helper path through the
server wrapper with ``ODOO_TOON_OUTPUT=1``.
"""

import unittest
from unittest.mock import MagicMock, Mock, patch

from odoo_sdk.commands.builtin import BUILTIN_COMMANDS, TimesheetSummaryCommand
from odoo_sdk.commands.command_registry import Registry
from odoo_sdk.mcp import server
from odoo_sdk.mcp.server import OdooMCPServer
from odoo_sdk.mcp.tools.atomic import make_timesheet_summary_tool

_HELPER = "odoo_sdk.commands.builtin.timesheet_summary.timesheet_summary"


class TestTimesheetSummaryCommand(unittest.TestCase):
    def test_registered_under_its_name(self):
        self.assertIs(BUILTIN_COMMANDS["timesheet_summary"], TimesheetSummaryCommand)
        self.assertEqual(TimesheetSummaryCommand(MagicMock()).name, "timesheet_summary")

    def test_delegates_to_helper_with_all_arguments(self):
        client = MagicMock()
        with patch(_HELPER, return_value={"ok": True}) as helper:
            result = TimesheetSummaryCommand(client).execute(
                "2026-07-01", "2026-07-31", group_by="client", only_mine=False
            )
        helper.assert_called_once_with(
            client,
            "2026-07-01",
            "2026-07-31",
            group_by="client",
            only_mine=False,
        )
        self.assertEqual(result, {"ok": True})

    def test_defaults_group_by_project_and_only_mine_true(self):
        client = MagicMock()
        with patch(_HELPER, return_value={}) as helper:
            TimesheetSummaryCommand(client).execute("2026-07-01", "2026-07-31")
        self.assertEqual(helper.call_args.kwargs, {"group_by": "project", "only_mine": True})


def _server_tool(name, tool_fn):
    """Register one explicit tool and return the resulting FastMCP ``Tool``."""
    registry = Registry(Mock())
    mock_mcp = MagicMock()
    added = []
    mock_mcp.add_tool.side_effect = added.append
    with patch("odoo_sdk.mcp.server.FastMCP", return_value=mock_mcp):
        OdooMCPServer(registry, explicit_tools={name: tool_fn})
    return added[0]


class _CommandRegistry:
    """Registry stub returning a real command wired to ``client``."""

    def __init__(self, client):
        self._client = client

    def __getitem__(self, name):
        return TimesheetSummaryCommand(self._client)


class TestTimesheetSummaryToonOutput(unittest.TestCase):
    def test_full_path_encodes_result_as_toon(self):
        client = MagicMock()
        client.execute.return_value = [
            {"project_id": [5, "Accounting"], "unit_amount": 8.0, "__count": 3},
        ]
        tool_fn = make_timesheet_summary_tool(_CommandRegistry(client))
        tool = _server_tool("timesheet_summary", tool_fn)
        with patch.dict("os.environ", {server.TOON_OUTPUT_ENV: "1"}):
            result = tool.fn("2026-07-01", "2026-07-31", "project", False)
        # TOON encodes the structured result to a string carrying its keys/values.
        self.assertIsInstance(result, str)
        self.assertIn("group_by", result)
        self.assertIn("total_hours", result)
        self.assertIn("Accounting", result)


if __name__ == "__main__":
    unittest.main()
