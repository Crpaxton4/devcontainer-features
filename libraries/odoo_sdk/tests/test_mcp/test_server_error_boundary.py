"""Tests for the MCP error boundary in :mod:`odoo_sdk.mcp.server`.

The boundary turns caller-actionable exceptions raised by a tool into one
uniform ``{"error": {"type", "message"}}`` payload so LLM callers never see a
raw traceback, while programming errors (``KeyError``, ``AttributeError``, ...)
still propagate. It is wired *inside* the TOON-encoding wrapper, so an error
payload is TOON-encoded like any other structured result.

The forced-failure ``odoo-mcp`` smoke described in issue #222 is replaced by the
server-integration cases below (a failing fake tool wired through a real
``OdooMCPServer``); no live Odoo is available in this environment.
"""

import asyncio
import unittest
from unittest.mock import MagicMock, Mock, patch

from odoo_sdk.commands.command_registry import Registry
from odoo_sdk.mcp import server
from odoo_sdk.mcp.server import OdooMCPServer, _error_boundary, _error_payload
from odoo_sdk.state.models import TaskAlreadyRunningError, TaskNotRunningError
from odoo_sdk.transport.errors import OdooError, OdooValidationError
from odoo_sdk.utilities.env import OdooDevcontainerRequiredError

# Each caught type paired with the exact payload the boundary must produce. The
# amendment to #222 uses TaskAlreadyRunningError in place of ActiveSessionError.
_CAUGHT_CASES = [
    (OdooError("odoo boom"), "OdooError", "odoo boom"),
    (TaskNotRunningError("no active session"), "TaskNotRunningError", "no active session"),
    (
        TaskAlreadyRunningError("session already active"),
        "TaskAlreadyRunningError",
        "session already active",
    ),
    (
        OdooDevcontainerRequiredError("not a devcontainer"),
        "OdooDevcontainerRequiredError",
        "not a devcontainer",
    ),
    (ValueError("bad input"), "ValueError", "bad input"),
]


def _sync_raiser(exc):
    """Return a sync tool callable that raises ``exc`` when invoked."""

    def tool():
        raise exc

    return tool


def _async_raiser(exc):
    """Return an async tool callable that raises ``exc`` when awaited."""

    async def tool():
        raise exc

    return tool


def _build_added(name, tool_fn):
    """Register one explicit tool and return the resulting FastMCP ``Tool``."""
    registry = Registry(Mock())
    mock_mcp = MagicMock()
    added = []
    mock_mcp.add_tool.side_effect = added.append
    with patch("odoo_sdk.mcp.server.FastMCP", return_value=mock_mcp):
        OdooMCPServer(registry, explicit_tools={name: tool_fn})
    return added[0]


class TestErrorPayload(unittest.TestCase):
    def test_payload_uses_concrete_type_name_and_message(self):
        payload = _error_payload(OdooError("boom"))
        self.assertEqual(payload, {"error": {"type": "OdooError", "message": "boom"}})

    def test_payload_reports_subclass_name_not_caught_base(self):
        # A mapped subclass keeps its own name so callers can still branch on it.
        payload = _error_payload(OdooValidationError("bad value"))
        self.assertEqual(
            payload, {"error": {"type": "OdooValidationError", "message": "bad value"}}
        )


class TestErrorBoundarySync(unittest.TestCase):
    def test_each_caught_type_returns_exact_payload(self):
        for exc, type_name, message in _CAUGHT_CASES:
            with self.subTest(type=type_name):
                wrapped = _error_boundary(_sync_raiser(exc))
                self.assertEqual(
                    wrapped(),
                    {"error": {"type": type_name, "message": message}},
                )

    def test_successful_result_passes_through_unchanged(self):
        wrapped = _error_boundary(lambda: {"ok": True})
        self.assertEqual(wrapped(), {"ok": True})

    def test_programmer_error_propagates(self):
        wrapped = _error_boundary(_sync_raiser(KeyError("missing")))
        with self.assertRaises(KeyError):
            wrapped()

    def test_attribute_error_propagates(self):
        wrapped = _error_boundary(_sync_raiser(AttributeError("nope")))
        with self.assertRaises(AttributeError):
            wrapped()


class TestErrorBoundaryAsync(unittest.TestCase):
    def test_each_caught_type_returns_exact_payload(self):
        for exc, type_name, message in _CAUGHT_CASES:
            with self.subTest(type=type_name):
                wrapped = _error_boundary(_async_raiser(exc))
                self.assertTrue(asyncio.iscoroutinefunction(wrapped))
                self.assertEqual(
                    asyncio.run(wrapped()),
                    {"error": {"type": type_name, "message": message}},
                )

    def test_successful_result_passes_through_unchanged(self):
        async def tool():
            return {"ok": True}

        wrapped = _error_boundary(tool)
        self.assertEqual(asyncio.run(wrapped()), {"ok": True})

    def test_programmer_error_propagates(self):
        wrapped = _error_boundary(_async_raiser(KeyError("missing")))
        with self.assertRaises(KeyError):
            asyncio.run(wrapped())


class TestErrorBoundaryWiredIntoServer(unittest.TestCase):
    """A failing fake tool wired through a real OdooMCPServer (smoke replacement)."""

    def test_sync_failing_tool_returns_structured_payload(self):
        tool = _build_added("boom", _sync_raiser(TaskNotRunningError("no active session")))
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(
                tool.fn(),
                {"error": {"type": "TaskNotRunningError", "message": "no active session"}},
            )

    def test_async_failing_tool_returns_structured_payload(self):
        tool = _build_added("boom", _async_raiser(TaskAlreadyRunningError("already active")))
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(
                asyncio.run(tool.fn()),
                {"error": {"type": "TaskAlreadyRunningError", "message": "already active"}},
            )

    def test_error_payload_is_toon_encoded_when_flag_on(self):
        # Boundary is inside the TOON wrapper, so the payload TOON-encodes too.
        tool = _build_added("boom", _sync_raiser(ValueError("bad input")))
        with patch.dict("os.environ", {server.TOON_OUTPUT_ENV: "1"}):
            result = tool.fn()
        self.assertIsInstance(result, str)
        self.assertIn("ValueError", result)
        self.assertIn("bad input", result)

    def test_programmer_error_still_propagates_through_server(self):
        tool = _build_added("boom", _sync_raiser(KeyError("bug")))
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(KeyError):
                tool.fn()


if __name__ == "__main__":
    unittest.main()
