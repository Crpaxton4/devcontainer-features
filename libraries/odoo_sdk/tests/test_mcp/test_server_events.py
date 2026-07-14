"""Tests for the MCP dispatch event wrapper (issue #326).

The ``_event_emitting`` wrapper is the *sole* event producer for the MCP tool
surface: every successful tool dispatch writes exactly one ``source="agent"``
event row, exceptions emit nothing, and telemetry failures never break the tool
call. These tests drive the wrapper through the full registration chain (built
by ``OdooMCPServer._register_tools``) and unit-test the small emission helpers.
"""

import asyncio
import inspect
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from odoo_sdk.commands import Registry
from odoo_sdk.mcp import server as server_mod
from odoo_sdk.mcp.server import OdooMCPServer
from odoo_sdk.state import LocalStateClient


def _tmp_db() -> LocalStateClient:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return LocalStateClient(db_path=Path(tmp.name))


def _build_tools(registry, explicit_tools):
    """Build a server with FastMCP mocked out; return {name: Tool} added."""
    mock_mcp = MagicMock()
    added = []
    mock_mcp.add_tool.side_effect = added.append
    with patch("odoo_sdk.mcp.server.FastMCP", return_value=mock_mcp):
        OdooMCPServer(registry, explicit_tools=explicit_tools)
    return {t.name: t for t in added}


class TestDispatchEmitsEvent(unittest.TestCase):
    def test_sync_tool_with_task_id_emits_one_event(self):
        db = _tmp_db()
        registry = Registry(Mock(), state_client=db)

        def do_thing(task_id: int, task_name: str) -> dict:
            """Fake tool."""
            return {"task_id": task_id}

        tools = _build_tools(registry, {"do_thing": do_thing})
        result = tools["do_thing"].fn(task_id=42, task_name="Fix VAT")

        self.assertEqual(result, {"task_id": 42})
        events = db.get_events()
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.source, "agent")
        self.assertEqual(event.subject, "do_thing: Fix VAT")
        self.assertEqual(event.task_ids, ["42"])
        self.assertEqual(
            event.payload,
            {"tool": "do_thing", "args": {"task_id": "42", "task_name": "Fix VAT"}},
        )

    def test_tool_without_task_id_has_empty_task_ids(self):
        db = _tmp_db()
        registry = Registry(Mock(), state_client=db)

        def ping(message: str) -> str:
            """Fake tool."""
            return message

        tools = _build_tools(registry, {"ping": ping})
        tools["ping"].fn(message="hi")

        events = db.get_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].task_ids, [])
        # No detail key present -> subject is the bare tool name.
        self.assertEqual(events[0].subject, "ping")

    def test_raising_tool_emits_no_event(self):
        db = _tmp_db()
        registry = Registry(Mock(), state_client=db)

        def boom(task_id: int) -> dict:
            """Fake tool."""
            raise ValueError("nope")

        tools = _build_tools(registry, {"boom": boom})
        result = tools["boom"].fn(task_id=1)

        # The boundary formats the error; the event wrapper (innermost) never
        # reached its emit because the exception propagated first.
        self.assertEqual(
            result, {"error": {"type": "ValueError", "message": "nope"}}
        )
        self.assertEqual(db.get_events(), [])

    def test_async_tool_emits_event(self):
        db = _tmp_db()
        registry = Registry(Mock(), state_client=db)

        async def do_async(task_id: int, note: str) -> dict:
            """Fake tool."""
            return {"ok": True}

        tools = _build_tools(registry, {"do_async": do_async})
        result = asyncio.run(tools["do_async"].fn(task_id=7, note="working"))

        self.assertEqual(result, {"ok": True})
        events = db.get_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].task_ids, ["7"])
        self.assertEqual(events[0].subject, "do_async: working")

    def test_emit_failure_does_not_break_tool(self):
        class BoomState:
            def add_event(self, event):
                raise RuntimeError("db down")

        registry = Registry(Mock(), state_client=BoomState())

        def do_thing(task_id: int) -> dict:
            """Fake tool."""
            return {"ok": task_id}

        tools = _build_tools(registry, {"do_thing": do_thing})
        # A raising state store must not surface to the caller.
        self.assertEqual(tools["do_thing"].fn(task_id=3), {"ok": 3})

    def test_signature_preserved_through_chain(self):
        db = _tmp_db()
        registry = Registry(Mock(), state_client=db)

        def do_thing(task_id: int, task_name: str = "x") -> dict:
            """Fake tool."""
            return {}

        tools = _build_tools(registry, {"do_thing": do_thing})
        self.assertEqual(
            inspect.signature(tools["do_thing"].fn),
            inspect.signature(do_thing),
        )

    def test_state_client_resolved_at_call_time_not_registration(self):
        # Register with no state store supplied, then inject one AFTER building
        # the server: the wrapper must resolve ``registry.state_client`` at call
        # time, so the injected store still receives the event.
        registry = Registry(Mock())

        def do_thing(task_id: int) -> dict:
            """Fake tool."""
            return {"ok": task_id}

        tools = _build_tools(registry, {"do_thing": do_thing})
        db = _tmp_db()
        registry._state_client = db
        tools["do_thing"].fn(task_id=9)
        self.assertEqual(len(db.get_events()), 1)


class TestEventHelpers(unittest.TestCase):
    def test_task_ids_coercible_int(self):
        self.assertEqual(server_mod._event_task_ids({"task_id": 5}), ["5"])

    def test_task_ids_coercible_str(self):
        self.assertEqual(server_mod._event_task_ids({"task_id": "5"}), ["5"])

    def test_task_ids_absent(self):
        self.assertEqual(server_mod._event_task_ids({}), [])

    def test_task_ids_none(self):
        self.assertEqual(server_mod._event_task_ids({"task_id": None}), [])

    def test_task_ids_non_coercible(self):
        self.assertEqual(server_mod._event_task_ids({"task_id": "abc"}), [])

    def test_subject_uses_first_present_detail(self):
        self.assertEqual(
            server_mod._event_subject("t", {"note": "n", "question": "q"}), "t: n"
        )

    def test_subject_truncates_detail_to_120(self):
        subject = server_mod._event_subject("t", {"description": "x" * 200})
        self.assertEqual(subject, "t: " + "x" * 120)

    def test_subject_bare_name_when_no_detail(self):
        self.assertEqual(server_mod._event_subject("t", {"foo": "bar"}), "t")

    def test_payload_filters_non_scalars_and_truncates(self):
        payload = server_mod._event_payload(
            "t", {"a": 1, "b": [1, 2], "c": "y" * 200}
        )
        self.assertEqual(
            payload, {"tool": "t", "args": {"a": "1", "c": "y" * 80}}
        )

    def test_bound_arguments_excludes_ctx(self):
        def sample(task_id, ctx=None):
            return None

        bound = server_mod._bound_arguments(
            inspect.signature(sample), (5,), {"ctx": object()}
        )
        self.assertEqual(bound, {"task_id": 5})


if __name__ == "__main__":
    unittest.main()
