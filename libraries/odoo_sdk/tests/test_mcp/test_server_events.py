"""Tests for the MCP dispatch event wrapper (issue #326).

The ``_event_emitting`` wrapper is the *sole* event producer for the MCP tool
surface: every successful tool dispatch writes exactly one ``source="agent"``
event row, exceptions and SEP-2322 input-required ask legs (#664) emit nothing,
and telemetry failures never break the tool call. These tests drive the wrapper
through the full registration chain (built by ``OdooMCPServer._register_tools``)
and unit-test the small emission helpers.
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
from tests.support import make_state_db

#: Patch targets for the best-effort git lookups ``LogEventCommand`` performs
#: when a caller leaves ``repo``/``branch`` unstated (#509).
REPO_LABEL = "odoo_sdk.commands.log_event.current_repo_label"
BRANCH_LABEL = "odoo_sdk.commands.log_event.current_branch_label"


def _tmp_db() -> LocalStateClient:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return make_state_db(Path(tmp.name))


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
        # Only the tool name is persisted as subject; no argument values.
        self.assertEqual(event.subject, "do_thing")
        self.assertEqual(event.task_ids, ["42"])
        # The payload is reconstructable (#626): the tool name, the argument
        # NAMES (shape, not content -- the task_name value "Fix VAT" is
        # withheld), a one-line outcome, and the allowlisted identifier fields
        # lifted from the result.
        self.assertEqual(
            event.payload,
            {
                "tool": "do_thing",
                "args": ["task_id", "task_name"],
                "outcome": "ok",
                "task_id": 42,
            },
        )

    def test_tool_without_task_id_and_no_active_run_is_untargeted(self):
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
        # Subject is the bare tool name; the "message" VALUE is not persisted
        # (only its name), and a non-dict result lifts no fields.
        self.assertEqual(events[0].subject, "ping")
        self.assertEqual(
            events[0].payload,
            {"tool": "ping", "args": ["message"], "outcome": "ok"},
        )

    def test_tool_without_task_id_attributes_to_the_active_run(self):
        # Regression for #507: attribution used to key on whether the tool's
        # signature happened to contain a parameter named ``task_id``, so an
        # inspection-only tool called mid-run wrote an event with an empty
        # ``task_ids`` -- permanently excluded from session derivation, and
        # therefore permanently unbillable. All interaction with a task is
        # active work on it, so the active run now claims the event.
        db = _tmp_db()
        db.create_run(101, "Task A", 1, "Proj")
        registry = Registry(Mock(), state_client=db)

        def report_runs(since: str) -> dict:
            """Fake tool."""
            return {"ok": True}

        tools = _build_tools(registry, {"report_runs": report_runs})
        tools["report_runs"].fn(since="today")

        self.assertEqual(db.get_events()[0].task_ids, ["101"])

    def test_explicit_task_id_still_wins_over_the_active_run(self):
        db = _tmp_db()
        db.create_run(101, "Task A", 1, "Proj")
        registry = Registry(Mock(), state_client=db)

        def do_thing(task_id: int) -> dict:
            """Fake tool."""
            return {"ok": True}

        tools = _build_tools(registry, {"do_thing": do_thing})
        tools["do_thing"].fn(task_id=999)

        self.assertEqual(db.get_events()[0].task_ids, ["999"])

    def test_provenance_is_resolved_not_hardcoded_empty(self):
        # Regression for #509: this path passed ``repo=""``, so no agent event
        # could ever be traced to the code that produced it and every derived
        # session fell back to the repo-less sentinel.
        db = _tmp_db()
        registry = Registry(Mock(), state_client=db)

        def ping() -> str:
            """Fake tool."""
            return "pong"

        tools = _build_tools(registry, {"ping": ping})
        with (
            patch(REPO_LABEL, return_value="o/r"),
            patch(BRANCH_LABEL, return_value="feat/kiosk"),
        ):
            tools["ping"].fn()

        event = db.get_events()[0]
        self.assertEqual(event.repo, "o/r")
        self.assertEqual(event.branch, "feat/kiosk")

    def test_free_text_arg_values_are_not_persisted(self):
        # Regression for #365: chatter note bodies, questions, and search
        # queries must never reach the local events store -- only the tool name
        # (+ task scope) is recorded.
        db = _tmp_db()
        registry = Registry(Mock(), state_client=db)
        secret = "expired coupons leak PII to the checkout log"

        def task_question(task_id: int, question: str) -> dict:
            """Fake tool."""
            return {"ok": True}

        tools = _build_tools(registry, {"task_question": task_question})
        tools["task_question"].fn(task_id=1234, question=secret)

        events = db.get_events()
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.subject, "task_question")
        # The payload carries argument NAMES only -- the question body never
        # reaches local persistence (#365), even in the enriched shape (#626).
        self.assertEqual(
            event.payload,
            {
                "tool": "task_question",
                "args": ["question", "task_id"],
                "outcome": "ok",
            },
        )
        self.assertEqual(event.task_ids, ["1234"])
        self.assertNotIn(secret, event.subject)
        self.assertNotIn(secret, repr(event.payload))

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
        # The note body ("working") is not persisted -- subject is the tool name
        # and the payload carries only names/outcome ("ok" is unlisted, so no
        # field is lifted).
        self.assertEqual(events[0].subject, "do_async")
        self.assertEqual(
            events[0].payload,
            {"tool": "do_async", "args": ["note", "task_id"], "outcome": "ok"},
        )

    def test_input_required_leg_emits_no_event(self):
        # #664: a SEP-2322 input-required first leg is an ask, not an outcome —
        # no agent event may be recorded for it (the continuation invocation
        # emits the dispatch's single event), and the ask must flow through the
        # wrapper chain to the wire unchanged.
        from mcp.types import (
            CreateMessageRequest,
            CreateMessageRequestParams,
            InputRequiredResult,
            SamplingMessage,
            TextContent,
        )

        db = _tmp_db()
        registry = Registry(Mock(), state_client=db)
        ask = InputRequiredResult(
            input_requests={
                "branch_description": CreateMessageRequest(
                    params=CreateMessageRequestParams(
                        messages=[
                            SamplingMessage(
                                role="user",
                                content=TextContent(type="text", text="prompt"),
                            )
                        ],
                        max_tokens=30,
                    )
                )
            }
        )

        async def start_task(task_id: int) -> dict:
            """Fake tool."""
            return ask

        tools = _build_tools(registry, {"start_task": start_task})
        result = asyncio.run(tools["start_task"].fn(task_id=10))

        self.assertIs(result, ask)
        self.assertEqual(db.get_events(), [])

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


class TestPayloadEnrichment(unittest.TestCase):
    """The #626 payload shape: reconstructable identifiers, never free text."""

    def test_identifier_fields_lifted_from_result(self):
        db = _tmp_db()
        registry = Registry(Mock(), state_client=db)

        def stop_task(task_id: int) -> dict:
            """Fake tool."""
            return {
                "run_id": 12,
                "elapsed_hours": 0.5,
                "branch_name": "4242#fix-vat",
                "pr_url": "https://github.com/o/r/pull/9",
                "test_result": "passed",
                "run_summary": "actions: task_note x2; branch 4242#fix-vat",
                "chatter": "free text that must NOT be lifted",
            }

        tools = _build_tools(registry, {"stop_task": stop_task})
        tools["stop_task"].fn(task_id=4242)

        payload = db.get_events()[0].payload
        self.assertEqual(payload["tool"], "stop_task")
        self.assertEqual(payload["args"], ["task_id"])
        self.assertEqual(payload["outcome"], "ok")
        self.assertEqual(payload["run_id"], 12)
        self.assertEqual(payload["elapsed_hours"], 0.5)
        self.assertEqual(payload["branch_name"], "4242#fix-vat")
        self.assertEqual(payload["pr_url"], "https://github.com/o/r/pull/9")
        self.assertEqual(payload["test_result"], "passed")
        # The machine-derived run summary is internal text with NO length cap
        # and rides along as the event's one-line outcome narrative.
        self.assertEqual(
            payload["run_summary"], "actions: task_note x2; branch 4242#fix-vat"
        )
        # Unlisted result keys (free text) never reach the payload.
        self.assertNotIn("chatter", payload)

    def test_none_and_non_scalar_result_fields_are_omitted(self):
        db = _tmp_db()
        registry = Registry(Mock(), state_client=db)

        def do_thing(task_id: int) -> dict:
            """Fake tool."""
            return {"run_id": None, "pr_url": ["not", "a", "scalar"], "state": "RUNNING"}

        tools = _build_tools(registry, {"do_thing": do_thing})
        tools["do_thing"].fn(task_id=1)

        payload = db.get_events()[0].payload
        self.assertNotIn("run_id", payload)
        self.assertNotIn("pr_url", payload)
        self.assertEqual(payload["state"], "RUNNING")

    def test_structured_error_result_records_error_outcome(self):
        # The wrapper only fires on a successful dispatch, but a tool handing
        # back a structured {"error": ...} payload is an outcome worth auditing.
        db = _tmp_db()
        registry = Registry(Mock(), state_client=db)

        def do_thing(task_id: int) -> dict:
            """Fake tool."""
            return {"error": {"type": "ValueError", "message": "bad input"}}

        tools = _build_tools(registry, {"do_thing": do_thing})
        tools["do_thing"].fn(task_id=1)

        self.assertEqual(db.get_events()[0].payload["outcome"], "error: bad input")

    def test_outcome_line_variants(self):
        self.assertEqual(server_mod._outcome_line({"ok": True}), "ok")
        self.assertEqual(server_mod._outcome_line("plain string"), "ok")
        self.assertEqual(server_mod._outcome_line(None), "ok")
        self.assertEqual(
            server_mod._outcome_line({"error": "boom"}), "error: boom"
        )
        self.assertEqual(server_mod._outcome_line({"error": {}}), "error")

    def test_result_payload_fields_ignores_non_dict(self):
        self.assertEqual(server_mod._result_payload_fields(None), {})
        self.assertEqual(server_mod._result_payload_fields([1, 2]), {})
        self.assertEqual(server_mod._result_payload_fields("x"), {})


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

    def test_bound_arguments_excludes_ctx(self):
        def sample(task_id, ctx=None):
            return None

        bound = server_mod._bound_arguments(
            inspect.signature(sample), (5,), {"ctx": object()}
        )
        self.assertEqual(bound, {"task_id": 5})


if __name__ == "__main__":
    unittest.main()
