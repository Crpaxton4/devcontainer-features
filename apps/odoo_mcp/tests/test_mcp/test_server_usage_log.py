"""Usage-log producers (#1204): the MCP wrapper and the ``_utils`` helpers.

The wrapper records success AND failure, names and outcome only, and a missing
``usage_log`` package changes nothing. The package itself is faked, because the
SDK has no dependency on it.
"""

import asyncio
import sys
import unittest
from unittest.mock import MagicMock, Mock, patch

from odoo_dev_core import telemetry
from odoo_mcp.server import OdooMCPServer
from ..support import make_service


def _tools(explicit_tools):
    mock_mcp = MagicMock()
    added = []
    mock_mcp.add_tool.side_effect = added.append
    with patch("odoo_mcp.server.FastMCP", return_value=mock_mcp):
        OdooMCPServer(make_service(Mock(), Mock()), explicit_tools=explicit_tools)
    return {t.name: t for t in added}


class TestMcpUsageLine(unittest.TestCase):
    def test_success_records_name_and_outcome_only(self):
        def ping(secret: str) -> dict:
            """Fake tool."""
            return {"ok": True}

        tools = _tools({"ping": ping})
        with patch("odoo_mcp.server.record_usage") as record:
            tools["ping"].fn(secret="s3cret")
        (call,) = record.call_args_list
        self.assertEqual(call.args, ("mcp_tool", "ping"))
        self.assertTrue(call.kwargs["ok"])
        self.assertEqual(call.kwargs["source"], "odoo_client")
        self.assertNotIn("s3cret", repr(call))

    def test_failure_is_recorded_and_still_raised(self):
        def boom() -> dict:
            """Fake tool."""
            raise RuntimeError("programming error")

        tools = _tools({"boom": boom})
        with patch("odoo_mcp.server.record_usage") as record:
            with self.assertRaises(RuntimeError):
                tools["boom"].fn()
        self.assertFalse(record.call_args.kwargs["ok"])

    def test_async_tool_is_recorded(self):
        async def aping() -> dict:
            """Fake async tool."""
            return {"ok": True}

        tools = _tools({"aping": aping})
        with patch("odoo_mcp.server.record_usage") as record:
            asyncio.run(tools["aping"].fn())
        self.assertEqual(record.call_args.args, ("mcp_tool", "aping"))

    def test_declarations_only_when_the_log_is_on(self):
        def ping() -> dict:
            """Fake tool."""
            return {}

        with patch("odoo_mcp.server.declare_usage") as declare:
            with patch.dict("os.environ", {"USAGE_LOG_DIR": ""}):
                _tools({"ping": ping})
            declare.assert_not_called()
            with patch.dict("os.environ", {"USAGE_LOG_DIR": "/x"}):
                _tools({"ping": ping})
        (items,) = declare.call_args.args
        self.assertIn(
            {
                "kind": "mcp_tool",
                "name": "ping",
                "context": {"source": "odoo_client"},
            },
            items,
        )


class TestUtilsHelpers(unittest.TestCase):
    def test_record_and_declare_delegate(self):
        fake = MagicMock()
        with patch.dict(sys.modules, {"usage_log": fake}):
            telemetry.record_usage("cli_command", "list", ok=True)
            telemetry.declare_usage([{"kind": "x", "name": "y"}])
        fake.record.assert_called_once_with("cli_command", "list", ok=True)
        fake.declare.assert_called_once_with([{"kind": "x", "name": "y"}])

    def test_missing_package_is_a_noop(self):
        with patch.dict(sys.modules, {"usage_log": None}):
            telemetry.record_usage("cli_command", "list")
            telemetry.declare_usage([])

    def test_a_raising_writer_never_propagates(self):
        fake = MagicMock()
        fake.record.side_effect = RuntimeError("disk")
        with patch.dict(sys.modules, {"usage_log": fake}):
            telemetry.record_usage("cli_command", "list")


if __name__ == "__main__":
    unittest.main()
