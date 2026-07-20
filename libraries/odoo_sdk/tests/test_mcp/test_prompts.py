"""Tests for MCP prompt registration and the implement_task prompt."""

import asyncio
import unittest
from unittest.mock import MagicMock, Mock, patch

from odoo_sdk.commands import Command, Registry
from odoo_sdk.mcp.prompts.builtin.implement_task import make_implement_task_prompt
from odoo_sdk.mcp.prompts.builtin.report_incident import report_incident
from odoo_sdk.mcp.server import OdooMCPServer
from odoo_sdk.utilities.prompt_messages import (
    build_implement_task_messages as _build_messages,
)


def _make_task(**overrides) -> dict:
    task = {
        "task_id": 42,
        "name": "Fix VAT calculation",
        "project": "Accounting",
        "stage": "In Progress",
        "assignees": ["Alice", "Bob"],
        "deadline": "2024-12-31",
        "priority": "1",
        "tags": ["bug", "tax"],
        "description": "Correct the rounding error in VAT.",
        "chatter": [
            {
                "id": 1,
                "date": "2024-01-01 10:00:00",
                "author": "Alice",
                "type": "comment",
                "subtype": "Discussions",
                "body": "Please fix ASAP.",
            }
        ],
    }
    task.update(overrides)
    return task


def _make_get_task_cmd(return_value):
    """Return a Command class whose execute() returns return_value."""
    rv = return_value

    class _GetTaskCmd(Command):
        _name = "get_task"
        _description = "mock get_task"

        def execute(self, task_id: int):
            return rv

    return _GetTaskCmd


def _registry_with_get_task(return_value) -> Registry:
    reg = Registry(Mock())
    reg.register("get_task", _make_get_task_cmd(return_value))
    return reg


def _empty_registry() -> Registry:
    return Registry(Mock())


class TestPromptRegistration(unittest.TestCase):
    """FastMCP prompt registration wired into OdooMCPServer."""

    def _build(self, registry: Registry):
        mock_mcp = MagicMock()
        captured: list = []
        mock_mcp.add_prompt.side_effect = captured.append
        with patch("odoo_sdk.mcp.server.FastMCP", return_value=mock_mcp):
            OdooMCPServer(registry)
        return mock_mcp, captured

    def _named(self, captured, name):
        return next(p for p in captured if p.name == name)

    def test_all_builtin_prompts_registered_on_server(self):
        _, captured = self._build(_empty_registry())
        self.assertEqual(len(captured), 8)

    def test_registered_prompt_is_a_prompt_instance(self):
        from fastmcp.prompts import Prompt

        _, captured = self._build(_empty_registry())
        self.assertIsInstance(captured[0], Prompt)

    def test_implement_task_registered_by_name(self):
        _, captured = self._build(_empty_registry())
        self.assertEqual(self._named(captured, "implement_task").name, "implement_task")

    def test_implement_task_has_description(self):
        _, captured = self._build(_empty_registry())
        prompt = self._named(captured, "implement_task")
        self.assertIsNotNone(prompt.description)
        self.assertIn("FSM", prompt.description)


class TestImplementTaskPromptFactory(unittest.TestCase):
    """make_implement_task_prompt factory and prompt invocation."""

    def test_raises_value_error_when_task_not_found(self):
        reg = _registry_with_get_task(None)
        fn = make_implement_task_prompt(reg)
        with self.assertRaises(ValueError) as cm:
            fn(task_id=999)
        self.assertIn("999", str(cm.exception))

    def test_returns_two_messages(self):
        reg = _registry_with_get_task(_make_task())
        fn = make_implement_task_prompt(reg)
        messages = fn(task_id=42)
        self.assertEqual(len(messages), 2)

    def test_all_messages_are_strings(self):
        reg = _registry_with_get_task(_make_task())
        fn = make_implement_task_prompt(reg)
        messages = fn(task_id=42)
        for msg in messages:
            self.assertIsInstance(msg, str)

    def test_calls_get_task_with_task_id(self):
        reg = _registry_with_get_task(_make_task())
        captured_ids: list = []

        class _TrackingCmd(Command):
            _name = "get_task"
            _description = "tracking"

            def execute(self, task_id: int):
                captured_ids.append(task_id)
                return _make_task()

        reg._commands["get_task"] = _TrackingCmd
        fn = make_implement_task_prompt(reg)
        fn(task_id=42)
        self.assertEqual(captured_ids, [42])


class TestBuildMessages(unittest.TestCase):
    """_build_messages message content."""

    def test_first_message_contains_task_id(self):
        msgs = _build_messages(_make_task())
        self.assertIn("42", msgs[0])

    def test_first_message_contains_task_name(self):
        msgs = _build_messages(_make_task())
        self.assertIn("Fix VAT calculation", msgs[0])

    def test_first_message_contains_project(self):
        msgs = _build_messages(_make_task())
        self.assertIn("Accounting", msgs[0])

    def test_first_message_contains_description(self):
        msgs = _build_messages(_make_task())
        self.assertIn("Correct the rounding error", msgs[0])

    def test_first_message_contains_chatter(self):
        msgs = _build_messages(_make_task())
        self.assertIn("Please fix ASAP", msgs[0])

    def test_second_message_contains_start_task_step(self):
        msgs = _build_messages(_make_task())
        self.assertIn("start_task", msgs[1])

    def test_second_message_contains_stop_task_step(self):
        msgs = _build_messages(_make_task())
        self.assertIn("stop_task", msgs[1])

    def test_second_message_contains_fsm_tool_table(self):
        msgs = _build_messages(_make_task())
        content = msgs[1]
        self.assertIn("task_note", content)
        self.assertIn("task_question", content)
        self.assertIn("resume_task", content)

    def test_second_message_mentions_guard_conditions(self):
        msgs = _build_messages(_make_task())
        content = msgs[1]
        self.assertIn("TaskAlreadyRunningError", content)
        self.assertIn("TaskNotRunningError", content)

    def test_second_message_embeds_task_id_in_tool_calls(self):
        msgs = _build_messages(_make_task())
        self.assertIn("task_note(42", msgs[1])
        self.assertIn("stop_task(42", msgs[1])

    def test_stop_step_does_not_ask_for_a_description(self):
        # Time logging moved to the odoo-tui/ETL path (#482), so the workflow
        # must not prompt for a timesheet-style work summary at STOP.
        content = _build_messages(_make_task())[1]
        self.assertIn("stop_task(42)", content)
        self.assertNotIn('stop_task(42, description="', content)

    def test_second_message_gives_note_style_guidance(self):
        msgs = _build_messages(_make_task())
        content = msgs[1]
        self.assertIn("Note Style", content)
        self.assertIn("2-4 short bullets", content)
        self.assertIn("one-line summary", content)

    def test_second_message_requires_python_unit_tests(self):
        msgs = _build_messages(_make_task())
        content = msgs[1]
        self.assertIn("**TEST**", content)
        self.assertIn("Python unit tests", content)
        self.assertIn("tests/", content)

    def test_second_message_requires_browser_tour_test(self):
        content = _build_messages(_make_task())[1]
        self.assertIn("browser tour test", content)

    def test_test_step_requires_running_tests(self):
        content = _build_messages(_make_task())[1]
        self.assertIn("RUN the tests", content)
        self.assertIn("REQUIRED", content)

    def test_test_step_precedes_stop_step(self):
        content = _build_messages(_make_task())[1]
        self.assertLess(content.index("**TEST**"), content.index("**STOP**"))

    def test_second_message_gives_concrete_note_cadence(self):
        content = _build_messages(_make_task())[1]
        self.assertIn("after each coherent", content)
        self.assertIn("after tests pass", content)
        self.assertIn("before you stop", content)

    def test_empty_chatter_shows_placeholder(self):
        task = _make_task(chatter=[])
        msgs = _build_messages(task)
        self.assertIn("(no messages)", msgs[0])

    def test_empty_description_shows_placeholder(self):
        task = _make_task(description="")
        msgs = _build_messages(task)
        self.assertIn("(no description)", msgs[0])

    def test_none_assignees_does_not_crash(self):
        task = _make_task(assignees=None)
        msgs = _build_messages(task)
        self.assertIn("—", msgs[0])

    def test_none_tags_does_not_crash(self):
        task = _make_task(tags=None)
        msgs = _build_messages(task)
        self.assertIn("—", msgs[0])

    def test_int_task_id_and_non_string_list_members_render(self):
        task = _make_task(task_id=27577, assignees=[101, "Bob"], tags=[7, "bug"])
        msgs = _build_messages(task)
        self.assertIn("27577", msgs[0])
        self.assertIn("101", msgs[0])
        self.assertIn("7", msgs[0])
        self.assertIn("task_note(27577", msgs[1])


class TestReportIncidentPromptRegistration(unittest.TestCase):
    """report_incident prompt is registered with the correct metadata."""

    def _build(self, registry: Registry):
        mock_mcp = MagicMock()
        captured: list = []
        mock_mcp.add_prompt.side_effect = captured.append
        with patch("odoo_sdk.mcp.server.FastMCP", return_value=mock_mcp):
            OdooMCPServer(registry)
        return mock_mcp, captured

    def _get_report_incident_prompt(self):
        from fastmcp.prompts import Prompt

        _, captured = self._build(_empty_registry())
        return next(p for p in captured if isinstance(p, Prompt) and p.name == "report_incident")

    def test_prompt_name_is_report_incident(self):
        prompt = self._get_report_incident_prompt()
        self.assertEqual(prompt.name, "report_incident")

    def test_prompt_has_description(self):
        prompt = self._get_report_incident_prompt()
        self.assertIsNotNone(prompt.description)
        self.assertGreater(len(prompt.description), 0)


class TestReportIncidentMessages(unittest.TestCase):
    """report_incident message content and privacy rules."""

    def test_returns_exactly_one_message(self):
        msgs = report_incident()
        self.assertEqual(len(msgs), 1)

    def test_message_is_a_string(self):
        msgs = report_incident()
        self.assertIsInstance(msgs[0], str)

    def test_message_contains_gh_issue_create(self):
        msgs = report_incident()
        self.assertIn("gh issue create", msgs[0])

    def test_message_contains_repo_url(self):
        msgs = report_incident()
        self.assertIn("https://github.com/Crpaxton4/devcontainer-features/", msgs[0])

    def test_message_contains_transport_env_value(self):
        with patch.dict("os.environ", {"ODOO_TRANSPORT": "json2"}):
            msgs = report_incident()
        self.assertIn("json2", msgs[0])

    def test_message_contains_sdk_version_tag(self):
        msgs = report_incident()
        self.assertIn("<sdk_version>", msgs[0])

    def test_message_contains_python_version_tag(self):
        msgs = report_incident()
        self.assertIn("<python_version>", msgs[0])

    def test_message_contains_privacy_guardrail(self):
        msgs = report_incident()
        self.assertIn("ODOO_URL", msgs[0])

    def test_description_argument_exposed_via_from_function(self):
        from fastmcp.prompts import Prompt

        prompt = Prompt.from_function(report_incident)
        arg_names = {arg.name for arg in (prompt.arguments or [])}
        self.assertIn("description", arg_names)

    def test_passed_description_appears_in_message(self):
        msgs = report_incident(description="boom")
        self.assertIn("boom", msgs[0])

    def test_default_description_omits_summary_section(self):
        msgs = report_incident()
        self.assertNotIn("Summary/description (pre-populated)", msgs[0])

    def test_description_preserves_privacy_and_env_block(self):
        msgs = report_incident(description="db exploded")
        self.assertIn("db exploded", msgs[0])
        self.assertIn("ODOO_URL", msgs[0])
        self.assertIn("<environment>", msgs[0])


class TestBuiltinPromptDecorator(unittest.TestCase):
    """``@builtin_prompt("name")`` populates ``BUILTIN_PROMPT_FACTORIES``."""

    def test_registers_the_shipped_prompts(self):
        from odoo_sdk.mcp.prompts.builtin import BUILTIN_PROMPT_FACTORIES

        # The decorator populates the registry at import time — no hand-edited
        # ``mcp.add_prompt(...)`` lines. Pin the set so a dropped/renamed
        # decorator fails here.
        self.assertEqual(
            set(BUILTIN_PROMPT_FACTORIES),
            {
                "client_status_report",
                "discovery_notes",
                "fibonacci_estimate",
                "implement_task",
                "odoo_code_review",
                "odoo_design_doc",
                "odoo_quote",
                "report_incident",
            },
        )

    def test_registration_order_is_import_order(self):
        # register_builtin_prompts iterates the registry in insertion order, which
        # follows the alphabetical import list in mcp/prompts/builtin/__init__.py.
        from odoo_sdk.mcp.prompts.builtin import BUILTIN_PROMPT_FACTORIES

        self.assertEqual(
            list(BUILTIN_PROMPT_FACTORIES),
            [
                "client_status_report",
                "discovery_notes",
                "fibonacci_estimate",
                "implement_task",
                "odoo_code_review",
                "odoo_design_doc",
                "odoo_quote",
                "report_incident",
            ],
        )

    def test_registers_factory_under_explicit_name(self):
        from odoo_sdk.mcp.prompts.builtin import (
            BUILTIN_PROMPT_FACTORIES,
            builtin_prompt,
        )

        def _factory(command_registry):  # pragma: no cover - never invoked
            return lambda: None

        with patch.dict(BUILTIN_PROMPT_FACTORIES, clear=False):
            returned = builtin_prompt("probe_prompt")(_factory)
            # The decorator is transparent and keys by the explicit name.
            self.assertIs(returned, _factory)
            self.assertIs(BUILTIN_PROMPT_FACTORIES["probe_prompt"], _factory)
        self.assertNotIn("probe_prompt", BUILTIN_PROMPT_FACTORIES)

    def test_duplicate_name_raises(self):
        from odoo_sdk.mcp.prompts.builtin import (
            BUILTIN_PROMPT_FACTORIES,
            builtin_prompt,
        )

        def _factory(command_registry):  # pragma: no cover - never invoked
            return lambda: None

        original = BUILTIN_PROMPT_FACTORIES["implement_task"]
        with self.assertRaises(ValueError) as ctx:
            builtin_prompt("implement_task")(_factory)
        self.assertIn("implement_task", str(ctx.exception))
        # The collision left the genuine factory in place (no silent overwrite).
        self.assertIs(BUILTIN_PROMPT_FACTORIES["implement_task"], original)

    def test_report_incident_factory_ignores_registry(self):
        from odoo_sdk.mcp.prompts.builtin.report_incident import (
            make_report_incident_prompt,
            report_incident,
        )

        # The factory returns the plain prompt callable regardless of the
        # registry it is handed (report_incident needs no command access).
        self.assertIs(make_report_incident_prompt(Mock()), report_incident)


class TestMigratedSkillPrompts(unittest.TestCase):
    """The 6 personal-features skills ported to built-in MCP prompts.

    Each is a ``report_incident``-shaped prompt: a plain callable returning the
    skill body as a one-element message list, and a factory that ignores the
    command registry. This drives them from their public modules so a rename or
    dropped decorator fails here.
    """

    # module/prompt name -> a phrase that must appear in the returned body.
    SKILLS = {
        "client_status_report": "timesheet_summary",
        "discovery_notes": "Gap analysis",
        "fibonacci_estimate": "Fibonacci",
        "odoo_code_review": "sudo()",
        "odoo_design_doc": "Record rules",
        "odoo_quote": "assumptions",
    }

    def _load(self, name):
        import importlib

        module = importlib.import_module(f"odoo_sdk.mcp.prompts.builtin.{name}")
        return getattr(module, name), getattr(module, f"make_{name}_prompt")

    def test_callable_returns_single_nonempty_message(self):
        for name in self.SKILLS:
            with self.subTest(name=name):
                fn, _ = self._load(name)
                msgs = fn()
                self.assertEqual(len(msgs), 1)
                self.assertIsInstance(msgs[0], str)
                self.assertTrue(msgs[0].strip())

    def test_body_contains_key_phrase(self):
        for name, phrase in self.SKILLS.items():
            with self.subTest(name=name):
                fn, _ = self._load(name)
                self.assertIn(phrase, fn()[0])

    def test_body_omits_frontmatter_and_feature_comment(self):
        for name in self.SKILLS:
            with self.subTest(name=name):
                fn, _ = self._load(name)
                body = fn()[0]
                self.assertNotIn("feature-managed", body)
                self.assertNotIn("\ndescription:", body)

    def test_factory_ignores_registry(self):
        for name in self.SKILLS:
            with self.subTest(name=name):
                fn, factory = self._load(name)
                self.assertIs(factory(Mock()), fn)

    def test_registered_on_server_with_description(self):
        from fastmcp.prompts import Prompt

        mock_mcp = MagicMock()
        captured: list = []
        mock_mcp.add_prompt.side_effect = captured.append
        with patch("odoo_sdk.mcp.server.FastMCP", return_value=mock_mcp):
            OdooMCPServer(_empty_registry())
        by_name = {p.name: p for p in captured if isinstance(p, Prompt)}
        for name in self.SKILLS:
            with self.subTest(name=name):
                self.assertIn(name, by_name)
                self.assertTrue(by_name[name].description)

    def test_registered_prompts_take_no_arguments(self):
        from fastmcp.prompts import Prompt

        for name in self.SKILLS:
            with self.subTest(name=name):
                fn, _ = self._load(name)
                prompt = Prompt.from_function(fn)
                self.assertFalse(prompt.arguments)


if __name__ == "__main__":
    unittest.main()
