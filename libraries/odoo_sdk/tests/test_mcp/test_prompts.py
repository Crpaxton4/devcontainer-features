"""Tests for MCP prompt registration and the implement_task prompt."""

import asyncio
import unittest
from unittest.mock import MagicMock, Mock, patch

from odoo_sdk.commands import Command, Registry
from odoo_sdk.mcp.prompts.builtin.implement_task import (
    _build_messages,
    make_implement_task_prompt,
)
from odoo_sdk.mcp.prompts.builtin.report_incident import report_incident
from odoo_sdk.mcp.server import OdooMCPServer


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

    def test_implement_task_registered_on_server(self):
        _, captured = self._build(_empty_registry())
        self.assertEqual(len(captured), 2)

    def test_registered_prompt_is_a_prompt_instance(self):
        from fastmcp.prompts import Prompt

        _, captured = self._build(_empty_registry())
        self.assertIsInstance(captured[0], Prompt)

    def test_registered_prompt_name_is_implement_task(self):
        _, captured = self._build(_empty_registry())
        self.assertEqual(captured[0].name, "implement_task")

    def test_registered_prompt_has_description(self):
        _, captured = self._build(_empty_registry())
        self.assertIsNotNone(captured[0].description)
        self.assertIn("FSM", captured[0].description)


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

    def test_all_messages_have_user_role(self):
        reg = _registry_with_get_task(_make_task())
        fn = make_implement_task_prompt(reg)
        messages = fn(task_id=42)
        for msg in messages:
            self.assertEqual(msg["role"], "user")

    def test_no_system_role_in_messages(self):
        reg = _registry_with_get_task(_make_task())
        fn = make_implement_task_prompt(reg)
        messages = fn(task_id=42)
        roles = {msg["role"] for msg in messages}
        self.assertNotIn("system", roles)

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
        self.assertIn("42", msgs[0]["content"])

    def test_first_message_contains_task_name(self):
        msgs = _build_messages(_make_task())
        self.assertIn("Fix VAT calculation", msgs[0]["content"])

    def test_first_message_contains_project(self):
        msgs = _build_messages(_make_task())
        self.assertIn("Accounting", msgs[0]["content"])

    def test_first_message_contains_description(self):
        msgs = _build_messages(_make_task())
        self.assertIn("Correct the rounding error", msgs[0]["content"])

    def test_first_message_contains_chatter(self):
        msgs = _build_messages(_make_task())
        self.assertIn("Please fix ASAP", msgs[0]["content"])

    def test_second_message_contains_start_task_step(self):
        msgs = _build_messages(_make_task())
        self.assertIn("start_task", msgs[1]["content"])

    def test_second_message_contains_stop_task_step(self):
        msgs = _build_messages(_make_task())
        self.assertIn("stop_task", msgs[1]["content"])

    def test_second_message_contains_fsm_tool_table(self):
        msgs = _build_messages(_make_task())
        content = msgs[1]["content"]
        self.assertIn("task_note", content)
        self.assertIn("task_question", content)
        self.assertIn("resume_task", content)

    def test_second_message_mentions_guard_conditions(self):
        msgs = _build_messages(_make_task())
        content = msgs[1]["content"]
        self.assertIn("TaskAlreadyRunningError", content)
        self.assertIn("TaskNotRunningError", content)

    def test_second_message_embeds_task_id_in_tool_calls(self):
        msgs = _build_messages(_make_task())
        self.assertIn("task_note(42", msgs[1]["content"])
        self.assertIn("stop_task(42", msgs[1]["content"])

    def test_empty_chatter_shows_placeholder(self):
        task = _make_task(chatter=[])
        msgs = _build_messages(task)
        self.assertIn("(no messages)", msgs[0]["content"])

    def test_empty_description_shows_placeholder(self):
        task = _make_task(description="")
        msgs = _build_messages(task)
        self.assertIn("(no description)", msgs[0]["content"])

    def test_none_assignees_does_not_crash(self):
        task = _make_task(assignees=None)
        msgs = _build_messages(task)
        self.assertIn("—", msgs[0]["content"])

    def test_none_tags_does_not_crash(self):
        task = _make_task(tags=None)
        msgs = _build_messages(task)
        self.assertIn("—", msgs[0]["content"])


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

    def test_message_role_is_user(self):
        msgs = report_incident()
        self.assertEqual(msgs[0]["role"], "user")

    def test_message_contains_gh_issue_create(self):
        msgs = report_incident()
        self.assertIn("gh issue create", msgs[0]["content"])

    def test_message_contains_repo_url(self):
        msgs = report_incident()
        self.assertIn("https://github.com/Crpaxton4/devcontainer-features/", msgs[0]["content"])

    def test_message_contains_transport_env_value(self):
        with patch.dict("os.environ", {"ODOO_TRANSPORT": "json2"}):
            msgs = report_incident()
        self.assertIn("json2", msgs[0]["content"])

    def test_message_contains_sdk_version_tag(self):
        msgs = report_incident()
        self.assertIn("<sdk_version>", msgs[0]["content"])

    def test_message_contains_python_version_tag(self):
        msgs = report_incident()
        self.assertIn("<python_version>", msgs[0]["content"])

    def test_message_contains_privacy_guardrail(self):
        msgs = report_incident()
        self.assertIn("ODOO_URL", msgs[0]["content"])


if __name__ == "__main__":
    unittest.main()
