"""Tests for the generic ``odoo-sdk cmd`` dispatcher (#713).

The dispatcher exposes every registry command on the CLI with one contract:
stdout is always exactly one JSON document. Success prints the raw command
result and exits 0; a boundary error prints the SAME
``{"error": {"type", "message"}}`` envelope the MCP error boundary renders
(shared via :mod:`odoo_sdk.commands.dispatch_telemetry`) and exits 1; a usage
error (unknown name, malformed ``--args``, bad kwargs) prints the same envelope
shape and exits 2. Exactly one ``source="agent"`` event with ``via: "cli"`` is
emitted per successful dispatch — never on failure — and local-only commands
dispatch without an Odoo client or env config ever being touched.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch

import odoo_sdk.cli.__main__ as cli
from odoo_sdk.commands import Command, Registry
from odoo_sdk.commands.builtin import register_builtins
from odoo_sdk.commands.dispatch_telemetry import _BOUNDARY_ERRORS
from odoo_sdk.errors import OdooError
from odoo_sdk.state import LocalStateClient
from odoo_sdk.state.models import (
    TaskAlreadyRunningError,
    TaskNotRunningError,
    TrackerStateMissingError,
)
from tests.support import make_state_db

BUILD_REGISTRY = "odoo_sdk.cli.__main__._build_registry"
ASSERT_GUARD = "odoo_sdk.cli.__main__.assert_sdk_configured"


def _tmp_db() -> LocalStateClient:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return make_state_db(Path(tmp.name))


def _run_cli(*argv: str) -> tuple[int, str]:
    """Run ``cli.main()`` with ``argv``; return ``(exit_code, stdout)``."""
    out = StringIO()
    code = 0
    with patch.object(sys, "argv", ["odoo-sdk", *argv]), redirect_stdout(out):
        try:
            cli.main()
        except SystemExit as exc:
            code = exc.code or 0
    return code, out.getvalue()


class _EchoCommand(Command):
    """Local fake command: echoes its one kwarg back."""

    _name = "echo"
    _description = "Echo the x kwarg back."

    def execute(self, x: int = 0) -> dict:
        return {"x": x}


def _raising_command(exc: BaseException) -> type[Command]:
    """Return a Command class whose execute raises ``exc``."""

    class _BoomCommand(Command):
        _name = "boom"
        _description = "Raise on execute."

        def execute(self, **kwargs):
            raise exc

    return _BoomCommand


def _registry(db, extra=None, client=None) -> Registry:
    """Real builtin registry over ``db``, with optional extra fake commands."""
    registry = register_builtins(
        Registry(client if client is not None else Mock(), state_client=db)
    )
    for name, command_cls in (extra or {}).items():
        registry.register(name, command_cls)
    return registry


def _patched(registry: Registry):
    """Patch the CLI's registry factory to hand back ``registry``."""
    return patch(BUILD_REGISTRY, lambda client, state=None, config=None: registry)


# ── cmd --list ────────────────────────────────────────────────────────────────


class TestCmdList(unittest.TestCase):
    def test_list_json_is_the_45_command_contract(self):
        # The contract-gate ground truth: every registry command — gated MCP
        # tools included (process trust) — as a {name, description} array.
        code, out = _run_cli("cmd", "--list", "--json")
        self.assertEqual(code, 0)
        entries = json.loads(out)
        self.assertEqual(len(entries), 45)
        for entry in entries:
            self.assertEqual(set(entry), {"name", "description"})
            self.assertIsInstance(entry["name"], str)
            self.assertIsInstance(entry["description"], str)
        names = [entry["name"] for entry in entries]
        self.assertEqual(names, sorted(names))
        # Gated MCP tools are exposed here; so is the CLI-only close_task.
        for gated in ("abort_run", "search_count", "get_models", "close_task"):
            self.assertIn(gated, names)

    def test_list_human_table(self):
        code, out = _run_cli("cmd", "--list")
        self.assertEqual(code, 0)
        self.assertIn("Command", out)
        self.assertIn("list_runs", out)
        with self.assertRaises(json.JSONDecodeError):
            json.loads(out)


# ── happy path + events ───────────────────────────────────────────────────────


class TestCmdDispatchSuccess(unittest.TestCase):
    def test_dispatch_with_args_prints_exactly_one_json_document(self):
        db = _tmp_db()
        with _patched(_registry(db, extra={"echo": _EchoCommand})):
            code, out = _run_cli("cmd", "echo", "--args", '{"x": 5}')
        self.assertEqual(code, 0)
        # json.loads over the whole stream: exactly ONE JSON document.
        self.assertEqual(json.loads(out), {"x": 5})

    def test_dispatch_without_args_defaults_to_no_kwargs(self):
        db = _tmp_db()
        with _patched(_registry(db)):
            code, out = _run_cli("cmd", "list_runs")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), [])

    def test_success_emits_exactly_one_agent_event_with_via_cli(self):
        db = _tmp_db()
        with _patched(_registry(db, extra={"echo": _EchoCommand})):
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

    def test_emit_failure_never_breaks_a_successful_dispatch(self):
        class BoomState:
            def add_event(self, event):
                raise RuntimeError("db down")

        registry = _registry(BoomState(), extra={"echo": _EchoCommand})
        with _patched(registry):
            code, out = _run_cli("cmd", "echo", "--args", '{"x": 1}')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"x": 1})


# ── boundary errors → envelope + exit 1 ───────────────────────────────────────


class TestCmdBoundaryErrors(unittest.TestCase):
    BOUNDARY_INSTANCES = [
        OdooError("odoo unreachable"),
        TaskNotRunningError("task 5 is not running"),
        TaskAlreadyRunningError("task 5 already running"),
        TrackerStateMissingError("no tracker database"),
        subprocess.CalledProcessError(1, ["git", "switch"]),
        ValueError("bad input"),
    ]

    def test_instances_cover_every_boundary_class(self):
        # Guard against drift: the shared tuple and this suite stay in lockstep.
        self.assertEqual(
            {type(exc) for exc in self.BOUNDARY_INSTANCES}, set(_BOUNDARY_ERRORS)
        )

    def test_each_boundary_error_renders_envelope_and_exits_1(self):
        for exc in self.BOUNDARY_INSTANCES:
            with self.subTest(error=type(exc).__name__):
                db = _tmp_db()
                registry = _registry(db, extra={"boom": _raising_command(exc)})
                with _patched(registry):
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
        with _patched(_registry(db)):
            code, out = _run_cli("cmd", "nope")
        self.assertEqual(code, 2)
        payload = json.loads(out)
        self.assertEqual(set(payload), {"error"})
        self.assertEqual(payload["error"]["type"], "ValueError")
        self.assertIn("nope", payload["error"]["message"])
        self.assertEqual(db.get_events(), [])

    def test_malformed_args_json_exits_2(self):
        db = _tmp_db()
        with _patched(_registry(db)):
            code, out = _run_cli("cmd", "list_runs", "--args", "{not json")
        self.assertEqual(code, 2)
        payload = json.loads(out)
        self.assertEqual(payload["error"]["type"], "ValueError")
        self.assertIn("malformed --args JSON", payload["error"]["message"])

    def test_non_object_args_json_exits_2(self):
        db = _tmp_db()
        with _patched(_registry(db)):
            code, out = _run_cli("cmd", "list_runs", "--args", "[1, 2]")
        self.assertEqual(code, 2)
        self.assertEqual(
            json.loads(out)["error"]["message"], "--args must be a JSON object"
        )

    def test_bad_kwargs_type_error_exits_2_and_emits_no_event(self):
        db = _tmp_db()
        with _patched(_registry(db, extra={"echo": _EchoCommand})):
            code, out = _run_cli("cmd", "echo", "--args", '{"unexpected": 1}')
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["error"]["type"], "TypeError")
        self.assertEqual(db.get_events(), [])

    def test_missing_name_without_list_exits_2(self):
        db = _tmp_db()
        with _patched(_registry(db)):
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
            patch("odoo_sdk.cli.__main__.OdooClient") as make_client,
            patch(ASSERT_GUARD, side_effect=AssertionError("env config touched")),
        ):
            registry = _registry(db, client=cli._LazyOdooClient())
            with _patched(registry):
                code, out = _run_cli("cmd", "list_runs")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), [])
        make_client.assert_not_called()

    def test_list_builds_no_state_and_no_client(self):
        # ``cmd --list`` walks the real (unpatched) registry factory: with the
        # OdooClient constructor patched to explode, listing still succeeds and
        # never constructs a client.
        with patch("odoo_sdk.cli.__main__.OdooClient") as make_client:
            code, out = _run_cli("cmd", "--list", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(out)), 45)
        make_client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
