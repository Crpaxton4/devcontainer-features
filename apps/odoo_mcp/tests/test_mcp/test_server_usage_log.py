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
from ..support import make_service, wire_tools


def _tools(fake_tools):
    return wire_tools(make_service(Mock(), Mock()), fake_tools)


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
        with patch("odoo_mcp.server.declare_usage") as declare:
            with patch.dict("os.environ", {"ODOO_DEV_USAGE_LOG": "0"}):
                _tools({})
            declare.assert_not_called()
            with patch.dict("os.environ", {"ODOO_DEV_USAGE_LOG": "1"}):
                _tools({})
        (items,) = declare.call_args.args
        declared = {i["name"] for i in items if i["kind"] == "mcp_tool"}
        self.assertIn(
            {
                "kind": "mcp_tool",
                "name": "get_uid",
                "context": {"source": "odoo_client"},
            },
            items,
        )
        self.assertNotIn("log_event", declared)
        self.assertEqual(len(declared), 32)


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
