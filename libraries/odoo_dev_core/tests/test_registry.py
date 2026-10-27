"""Tests for the string-keyed command registry and ``CommandService``."""

import dataclasses
import unittest
from dataclasses import dataclass
from typing import Any

from odoo_dev_core import (
    CommandRegistry,
    CommandService,
    CommandSpec,
    Services,
    default_registry,
)
from odoo_dev_core.registry import CommandArgumentError

from .fakes import fake_services


@dataclass(frozen=True)
class _EchoArgs:
    text: str
    times: int = 1


def _echo(services: Services, args: _EchoArgs) -> dict[str, Any]:
    return {"services": services, "args": args}


def _spec(id: str = "echo", **overrides: Any) -> CommandSpec:
    fields = dict(id=id, handler=_echo, args=_EchoArgs, description="Echo it.")
    fields.update(overrides)
    return CommandSpec(**fields)


class TestCommandRegistry(unittest.TestCase):
    def test_register_and_get(self):
        registry = CommandRegistry()
        spec = _spec()
        registry.register(spec)
        self.assertIs(registry.get("echo"), spec)

    def test_duplicate_id_raises_value_error(self):
        registry = CommandRegistry()
        registry.register(_spec())
        with self.assertRaises(ValueError) as ctx:
            registry.register(_spec())
        self.assertIn("echo", str(ctx.exception))

    def test_get_unknown_id_raises_key_error(self):
        with self.assertRaises(KeyError):
            CommandRegistry().get("nope")

    def test_ids_are_sorted(self):
        registry = CommandRegistry()
        for id in ("b", "c", "a"):
            registry.register(_spec(id))
        self.assertEqual(registry.ids(), ["a", "b", "c"])

    def test_specs_sorted_and_filtered_by_surface(self):
        registry = CommandRegistry()
        registry.register(_spec("b"))
        registry.register(_spec("a", surfaces=frozenset({"cli"})))
        registry.register(_spec("c", surfaces=frozenset({"mcp"})))
        self.assertEqual([s.id for s in registry.specs()], ["a", "b", "c"])
        self.assertEqual([s.id for s in registry.specs("cli")], ["a", "b"])
        self.assertEqual([s.id for s in registry.specs("mcp")], ["b", "c"])
        self.assertEqual(registry.specs("tui"), [])

    def test_spec_defaults(self):
        spec = _spec()
        self.assertFalse(spec.gated)
        self.assertEqual(spec.surfaces, frozenset({"cli", "mcp"}))

    def test_spec_is_frozen(self):
        with self.assertRaises(dataclasses.FrozenInstanceError):
            _spec().id = "other"  # type: ignore[misc]


class TestCommandService(unittest.TestCase):
    def setUp(self):
        self.registry = CommandRegistry()
        self.registry.register(_spec())
        self.services = fake_services()
        self.service = CommandService(self.registry, self.services)

    def test_exposes_registry_and_services_read_only(self):
        self.assertIs(self.service.registry, self.registry)
        self.assertIs(self.service.services, self.services)
        with self.assertRaises(AttributeError):
            self.service.registry = CommandRegistry()  # type: ignore[misc]

    def test_handler_receives_services_and_args(self):
        result = self.service.execute("echo", text="hi", times=2)
        self.assertIs(result["services"], self.services)
        self.assertEqual(result["args"], _EchoArgs(text="hi", times=2))

    def test_defaults_come_from_the_args_dataclass(self):
        self.assertEqual(self.service.execute("echo", text="x")["args"].times, 1)

    def test_unknown_id_raises_key_error(self):
        with self.assertRaises(KeyError):
            self.service.execute("missing")

    def test_unknown_field_raises_value_error_naming_id_and_fields(self):
        with self.assertRaises(ValueError) as ctx:
            self.service.execute("echo", text="x", bogus=1)
        message = str(ctx.exception)
        self.assertIn("echo", message)
        self.assertIn("bogus", message)
        self.assertIn("text, times", message)

    def test_missing_field_raises_value_error_naming_id(self):
        with self.assertRaises(ValueError) as ctx:
            self.service.execute("echo")
        self.assertIn("echo", str(ctx.exception))
        self.assertIsInstance(ctx.exception, CommandArgumentError)

    def test_a_handler_value_error_is_not_an_argument_error(self):
        def boom(services: Services, args: _EchoArgs) -> None:
            raise ValueError("inside the handler")

        self.registry.register(_spec("boom", handler=boom))
        with self.assertRaises(ValueError) as ctx:
            self.service.execute("boom", text="x")
        self.assertNotIsInstance(ctx.exception, CommandArgumentError)

    def test_id_is_positional_only_so_a_field_may_be_called_id(self):
        @dataclass(frozen=True)
        class _IdArgs:
            id: int

        self.registry.register(
            _spec("by_id", handler=lambda services, args: args.id, args=_IdArgs)
        )
        self.assertEqual(self.service.execute("by_id", id=7), 7)


EXPECTED_CLI_ONLY = {"log_event", "upload", "get_employee_id"}


class TestDefaultRegistry(unittest.TestCase):
    def test_holds_exactly_the_35_builtin_ids(self):
        ids = default_registry().ids()
        self.assertEqual(len(ids), 35)
        self.assertEqual(ids, sorted(ids))

    def test_every_args_is_a_frozen_dataclass(self):
        for spec in default_registry().specs():
            with self.subTest(command=spec.id):
                self.assertTrue(dataclasses.is_dataclass(spec.args))
                self.assertTrue(spec.args.__dataclass_params__.frozen)

    def test_every_description_is_non_empty(self):
        for spec in default_registry().specs():
            with self.subTest(command=spec.id):
                self.assertTrue(spec.description.strip())

    def test_cli_only_commands(self):
        registry = default_registry()
        cli_only = {spec.id for spec in registry.specs() if spec.surfaces == {"cli"}}
        self.assertEqual(cli_only, EXPECTED_CLI_ONLY)
        self.assertEqual(len(registry.specs("mcp")), 32)
        self.assertEqual(len(registry.specs("cli")), 35)

    def test_only_get_models_is_gated(self):
        gated = {spec.id for spec in default_registry().specs() if spec.gated}
        self.assertEqual(gated, {"get_models"})

    def test_each_call_returns_an_independent_registry(self):
        first, second = default_registry(), default_registry()
        self.assertIsNot(first, second)
        self.assertEqual(first.ids(), second.ids())

    def test_every_spec_handler_takes_services_and_args(self):
        for spec in default_registry().specs():
            with self.subTest(command=spec.id):
                self.assertTrue(callable(spec.handler))
                self.assertEqual(spec.handler.__name__, "handle")


if __name__ == "__main__":
    unittest.main()
