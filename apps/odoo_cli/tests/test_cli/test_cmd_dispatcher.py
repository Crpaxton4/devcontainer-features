"""Tests for the generic ``odoo-dev cmd`` dispatcher (#713).

The dispatcher exposes every registry command on the CLI with one contract:
stdout is always exactly one JSON document. Success prints the raw command
result and exits 0; a boundary error prints the SAME
``{"error": {"type", "message"}}`` envelope the MCP error boundary renders
(shared via :mod:`odoo_dev_core.telemetry`) and exits 1; a usage
error (unknown name, malformed ``--args``, bad kwargs) prints the same envelope
shape and exits 2. Exactly one ``source="agent"`` event with ``via: "cli"`` is
emitted per successful dispatch — never on failure — and local-only commands
dispatch without an Odoo client or env config ever being touched.
"""

import json
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from io import StringIO
from dataclasses import dataclass
from unittest.mock import Mock, patch

import odoo_cli.__main__ as cli
from odoo_dev_core import CommandRegistry, CommandService, CommandSpec, default_registry
from odoo_dev_core.telemetry import BOUNDARY_ERRORS, READ_TASK_IDS_PAYLOAD_KEY
from odoo_client.errors import OdooError
from odoo_dev_data.events import EventStore
from ..support import make_event_store, make_service

BUILD_SERVICE = "odoo_cli.__main__._build_service"
ASSERT_GUARD = "odoo_cli.__main__.assert_configured"


def _tmp_db() -> EventStore:
    return make_event_store()


def _run_cli(*argv: str) -> tuple[int, str]:
    """Run ``cli.main()`` with ``argv``; return ``(exit_code, stdout)``."""
    out = StringIO()
    code = 0
    with patch.object(sys, "argv", ["odoo-dev", *argv]), redirect_stdout(out):
        try:
            cli.main()
        except SystemExit as exc:
            code = exc.code or 0
    return code, out.getvalue()


@dataclass(frozen=True)
class _EchoArgs:
    x: int = 0


def _echo(services, args: _EchoArgs) -> dict:
    return {"x": args.x}


#: Local fake command: echoes its one kwarg back.
_ECHO = CommandSpec("echo", _echo, _EchoArgs, "Echo the x kwarg back.")


@dataclass(frozen=True)
class _ReadTaskArgs:
    task_id: int = 0


def _read_task(services, args: _ReadTaskArgs) -> dict:
    return {"id": args.task_id}


#: Local fake standing in for a READ tool that takes a ``task_id``. Registered
#: under the real ``get_task`` id so the dispatcher's telemetry classifies it the
#: way the live command would (#1040), without needing an Odoo client.
_READ_TASK = CommandSpec("get_task", _read_task, _ReadTaskArgs, "Read one task.")


@dataclass(frozen=True)
class _NoArgs:
    pass


def _raising_spec(exc: BaseException) -> CommandSpec:
    """Return a spec whose handler raises ``exc``."""

    def boom(services, args):
        raise exc

    return CommandSpec("boom", boom, _NoArgs, "Raise on execute.")


def _service(db, extra=(), client=None) -> CommandService:
    """Real builtin registry over ``db``, with optional extra fake commands.

    An extra spec replaces a builtin of the same id.
    """
    overridden = {spec.id for spec in extra}
    registry = CommandRegistry()
    for spec in default_registry().specs():
        if spec.id not in overridden:
            registry.register(spec)
    for spec in extra:
        registry.register(spec)
    base = make_service(db, client if client is not None else Mock())
    return CommandService(registry, base.services)


def _patched(service: CommandService):
    """Patch the CLI's service factory to hand back ``service``."""
    return patch(BUILD_SERVICE, lambda: service)


# ── cmd --list ────────────────────────────────────────────────────────────────


class TestCmdList(unittest.TestCase):
    def test_list_json_is_the_35_command_contract(self):
        # The contract-gate ground truth: every registry command — gated MCP
        # tools included (process trust) — as a {name, description} array.
        code, out = _run_cli("cmd", "--list", "--json")
        self.assertEqual(code, 0)
        entries = json.loads(out)
        self.assertEqual(len(entries), 35)
        for entry in entries:
            self.assertEqual(set(entry), {"name", "description"})
            self.assertIsInstance(entry["name"], str)
            self.assertIsInstance(entry["description"], str)
        names = [entry["name"] for entry in entries]
        self.assertEqual(names, sorted(names))
        for name in ("search_count", "get_models", "resync", "upload", "log_event"):
            self.assertIn(name, names)

    def test_list_human_table(self):
        code, out = _run_cli("cmd", "--list")
        self.assertEqual(code, 0)
        self.assertIn("Command", out)
        self.assertIn("query_sessions", out)
        with self.assertRaises(json.JSONDecodeError):
            json.loads(out)


# ── happy path + events ───────────────────────────────────────────────────────


class TestCmdDispatchSuccess(unittest.TestCase):
    def test_dispatch_with_args_prints_exactly_one_json_document(self):
        db = _tmp_db()
        with _patched(_service(db, extra=(_ECHO,))):
            code, out = _run_cli("cmd", "echo", "--args", '{"x": 5}')
        self.assertEqual(code, 0)
        # json.loads over the whole stream: exactly ONE JSON document.
        self.assertEqual(json.loads(out), {"x": 5})

    def test_dispatch_without_args_defaults_to_no_kwargs(self):
        db = _tmp_db()
        with _patched(_service(db)):
            code, out = _run_cli("cmd", "query_sessions")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), [])

    def test_success_emits_exactly_one_agent_event_with_via_cli(self):
        db = _tmp_db()
        with _patched(_service(db, extra=(_ECHO,))):
            code, _ = _run_cli("cmd", "echo", "--args", '{"x": 7}')
        self.assertEqual(code, 0)
        events = db.get_events()
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.source, "agent")
        self.assertEqual(event.subject, "echo")
        # Same #626 payload shape as the MCP wrapper — argument NAMES only, one
        # outcome line — plus the via marker naming the dispatch surface.
        self.assertEqual(
            event.payload,
            {"tool": "echo", "args": ["x"], "outcome": "ok", "via": "cli"},
        )

    def test_read_dispatch_records_the_queried_id_without_attributing(self):
        # #1040 on the CLI dispatch surface: ``odoo-dev cmd get_task`` is just as
        # capable of minting a phantom 0.25 h session as the MCP tool was, so the
        # queried id lands in the payload and NOT in ``task_ids``.
        db = _tmp_db()
        with _patched(_service(db, extra=(_READ_TASK,))):
            code, _ = _run_cli("cmd", "get_task", "--args", '{"task_id": 42}')
        self.assertEqual(code, 0)
        event = db.get_events()[0]
        self.assertEqual(event.task_ids, [])
        self.assertEqual(
            event.payload,
            {
                "tool": "get_task",
                "args": ["task_id"],
                "outcome": "ok",
                "via": "cli",
                READ_TASK_IDS_PAYLOAD_KEY: ["42"],
            },
        )

    def test_emit_failure_never_breaks_a_successful_dispatch(self):
        class BoomState:
            def add_event(self, event):
                raise RuntimeError("db down")

        service = _service(BoomState(), extra=(_ECHO,))
        with _patched(service):
            code, out = _run_cli("cmd", "echo", "--args", '{"x": 1}')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"x": 1})


# ── boundary errors → envelope + exit 1 ───────────────────────────────────────


class TestCmdBoundaryErrors(unittest.TestCase):
    BOUNDARY_INSTANCES = [
        OdooError("odoo unreachable"),
        subprocess.CalledProcessError(1, ["git", "switch"]),
        ValueError("bad input"),
    ]

    def test_instances_cover_every_boundary_class(self):
        # Guard against drift: the shared tuple and this suite stay in lockstep.
        self.assertEqual(
            {type(exc) for exc in self.BOUNDARY_INSTANCES}, set(BOUNDARY_ERRORS)
        )

    def test_each_boundary_error_renders_envelope_and_exits_1(self):
        for exc in self.BOUNDARY_INSTANCES:
            with self.subTest(error=type(exc).__name__):
                db = _tmp_db()
                service = _service(db, extra=(_raising_spec(exc),))
                with _patched(service):
                    code, out = _run_cli("cmd", "boom")
                self.assertEqual(code, 1)
                self.assertEqual(
                    json.loads(out),
                    {"error": {"type": type(exc).__name__, "message": str(exc)}},
                )
                # No event on failure.
                self.assertEqual(db.get_events(), [])


# ── usage errors → envelope + exit 2 ──────────────────────────────────────────


class TestCmdUsageErrors(unittest.TestCase):
    def test_unknown_command_name_exits_2(self):
        db = _tmp_db()
        with _patched(_service(db)):
            code, out = _run_cli("cmd", "nope")
        self.assertEqual(code, 2)
        payload = json.loads(out)
        self.assertEqual(set(payload), {"error"})
        self.assertEqual(payload["error"]["type"], "ValueError")
        self.assertIn("nope", payload["error"]["message"])
        self.assertEqual(db.get_events(), [])

    def test_malformed_args_json_exits_2(self):
        db = _tmp_db()
        with _patched(_service(db)):
            code, out = _run_cli("cmd", "query_sessions", "--args", "{not json")
        self.assertEqual(code, 2)
        payload = json.loads(out)
        self.assertEqual(payload["error"]["type"], "ValueError")
        self.assertIn("malformed --args JSON", payload["error"]["message"])

    def test_non_object_args_json_exits_2(self):
        db = _tmp_db()
        with _patched(_service(db)):
            code, out = _run_cli("cmd", "query_sessions", "--args", "[1, 2]")
        self.assertEqual(code, 2)
        self.assertEqual(
            json.loads(out)["error"]["message"], "--args must be a JSON object"
        )

    def test_bad_kwargs_exit_2_and_emit_no_event(self):
        db = _tmp_db()
        with _patched(_service(db, extra=(_ECHO,))):
            code, out = _run_cli("cmd", "echo", "--args", '{"unexpected": 1}')
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["error"]["type"], "CommandArgumentError")
        self.assertEqual(db.get_events(), [])

    def test_missing_name_without_list_exits_2(self):
        db = _tmp_db()
        with _patched(_service(db)):
            code, out = _run_cli("cmd")
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["error"]["type"], "ValueError")


# ── lazy client: local-only commands never touch Odoo ─────────────────────────


class TestCmdLazyClient(unittest.TestCase):
    def test_local_only_command_dispatches_with_no_odoo_config(self):
        # A local-only command must dispatch through the REAL lazy-client path:
        # no OdooClient construction, no _assert_env capability check. The
        # env guard is patched to explode so any call to it fails the test.
        db = _tmp_db()
        with (
            patch("odoo_cli.__main__.OdooClient") as make_client,
            patch(ASSERT_GUARD, side_effect=AssertionError("env config touched")),
        ):
            service = _service(db, client=cli._LazyOdooClient())
            with _patched(service):
                code, out = _run_cli("cmd", "query_sessions")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), [])
        make_client.assert_not_called()

    def test_list_builds_no_state_and_no_client(self):
        # ``cmd --list`` walks the real (unpatched) service factory: with the
        # OdooClient constructor patched to explode, listing still succeeds and
        # never constructs a client.
        with patch("odoo_cli.__main__.OdooClient") as make_client:
            code, out = _run_cli("cmd", "--list", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(out)), 35)
        make_client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
