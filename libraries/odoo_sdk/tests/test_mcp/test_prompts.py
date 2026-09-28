"""Tests for MCP prompt registration and the implement_task prompt."""

import asyncio
import unittest
from unittest.mock import MagicMock, Mock, patch

from odoo_sdk.commands import Command, Registry
from odoo_sdk.commands.command import MAX_CHATTER_BODY_CHARS
from odoo_sdk.commands.builtin.get_task import GetTaskCommand
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
    """Return a Command class whose execute() returns return_value.

    Mirrors the real :class:`GetTaskCommand` contract: ``chatter`` is opt-in via
    ``include``, so a caller that forgets it gets a task without chatter — the
    stub cannot hide the omission the way an always-chatter stub did.
    """
    rv = return_value

    class _GetTaskCmd(Command):
        _name = "get_task"
        _description = "mock get_task"

        def execute(self, task_id: int, include: list[str] | None = None):
            if rv is None:
                return None
            task = dict(rv)
            if include is None or "chatter" not in include:
                task.pop("chatter", None)
            return task

    return _GetTaskCmd


def _tracking_get_task_cmd(calls: list):
    """Return a get_task Command class recording each (task_id, include) call."""

    class _TrackingCmd(Command):
        _name = "get_task"
        _description = "tracking"

        def execute(self, task_id: int, include: list[str] | None = None):
            calls.append((task_id, include))
            return _make_task()

    return _TrackingCmd


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
        # 2 since #784 moved the five static consulting prompts to the
        # odoo-dev plugin's skills; only the dynamic prompts remain.
        _, captured = self._build(_empty_registry())
        self.assertEqual(len(captured), 2)

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

    def setUp(self):
        #: (task_id, include) recorded by ``_tracking_get_task_cmd``.
        self.calls: list[tuple[int, list[str] | None]] = []

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
        reg._commands["get_task"] = _tracking_get_task_cmd(self.calls)
        fn = make_implement_task_prompt(reg)
        fn(task_id=42)
        self.assertEqual([task_id for task_id, _ in self.calls], [42])

    def test_opts_into_description_and_chatter(self):
        # Both sections are opt-in on the real command; without this include the
        # rendered prompt silently loses all chatter.
        reg = _registry_with_get_task(_make_task())
        reg._commands["get_task"] = _tracking_get_task_cmd(self.calls)
        fn = make_implement_task_prompt(reg)
        fn(task_id=42)
        self.assertEqual(self.calls[0][1], ["description", "chatter"])

    def test_rendered_prompt_contains_chatter(self):
        reg = _registry_with_get_task(_make_task())
        fn = make_implement_task_prompt(reg)
        messages = fn(task_id=42)
        self.assertIn("Please fix ASAP", messages[0])
        self.assertNotIn("(no messages)", messages[0])


class _FakeOdooClient:
    """Minimal ``search_read`` client backing the real ``GetTaskCommand``.

    Records every ``(model, method)`` pair so a test can assert which RPCs the
    prompt actually triggered.
    """

    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def execute(self, model: str, method: str, *args, **kwargs):
        self.calls.append((model, method))
        if model == "project.task":
            return [
                {
                    "id": 42,
                    "name": "Fix VAT calculation",
                    "project_id": [3, "Accounting"],
                    "stage_id": [7, "In Progress"],
                    "user_ids": [],
                    "date_deadline": "2024-12-31",
                    "priority": "1",
                    "tag_ids": [],
                    "description": "<p>Correct the rounding error in VAT.</p>",
                }
            ]
        if model == "mail.message":
            return [
                {
                    "id": 1,
                    "date": "2024-01-01 10:00:00",
                    "author_id": [5, "Alice"],
                    "message_type": "comment",
                    "subtype_id": [1, "Discussions"],
                    "body": "<p>Please fix ASAP.</p>",
                }
            ]
        raise AssertionError(f"unexpected model {model}")  # pragma: no cover


class TestImplementTaskPromptAgainstRealCommand(unittest.TestCase):
    """The prompt driven through the real GetTaskCommand, not a stub.

    Regression guard for the chatter drop: ``get_task`` gates chatter behind
    ``include``, so only an end-to-end wiring proves the prompt opts in.
    """

    def _render(self):
        client = _FakeOdooClient()
        reg = Registry(client)
        reg.register("get_task", GetTaskCommand)
        messages = make_implement_task_prompt(reg)(task_id=42)
        return client, messages

    def test_rendered_prompt_includes_chatter_body(self):
        _, messages = self._render()
        self.assertIn("Please fix ASAP", messages[0])

    def test_rendered_prompt_has_no_empty_chatter_placeholder(self):
        _, messages = self._render()
        self.assertNotIn("(no messages)", messages[0])

    def test_chatter_rpc_is_actually_issued(self):
        client, _ = self._render()
        self.assertIn(("mail.message", "search_read"), client.calls)

    def test_rendered_prompt_still_includes_description(self):
        _, messages = self._render()
        self.assertIn("Correct the rounding error", messages[0])


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
        # #621: start_task is idempotent, so the prompt documents the
        # already_running flag instead of a TaskAlreadyRunningError guard.
        self.assertNotIn("TaskAlreadyRunningError", content)
        self.assertIn("already_running", content)
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

    def test_note_style_states_chatter_is_client_visible(self):
        # #767: the attachment affordance was documented with no statement of
        # audience, so agents posted internal artifacts to a customer thread.
        content = _build_messages(_make_task())[1]
        start = content.index("## Note Style")
        style = content[start : content.index("## Tool Reference")]
        self.assertIn("client-visible", style)
        self.assertIn("deliverables the client asked to receive", style)
        self.assertIn("scripts, logs", style)

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

    def test_review_step_runs_coderabbit(self):
        content = _build_messages(_make_task())[1]
        self.assertIn("**REVIEW**", content)
        self.assertIn("coderabbit review", content)

    def test_review_step_between_test_and_stop(self):
        content = _build_messages(_make_task())[1]
        self.assertLess(content.index("**TEST**"), content.index("**REVIEW**"))
        self.assertLess(content.index("**REVIEW**"), content.index("**STOP**"))

    def test_review_step_is_required_not_optional(self):
        content = _build_messages(_make_task())[1]
        review = content[content.index("**REVIEW**") : content.index("**STOP**")]
        self.assertIn("REQUIRED", review)

    def test_review_findings_are_untrusted(self):
        content = _build_messages(_make_task())[1]
        review = content[content.index("**REVIEW**") : content.index("**STOP**")]
        self.assertIn("untrusted", review)
        self.assertIn("NEVER execute", review)

    def test_signed_out_cli_is_hard_failure(self):
        content = _build_messages(_make_task())[1]
        review = content[content.index("**REVIEW**") : content.index("**STOP**")]
        self.assertIn("signed-out", review)
        self.assertIn("not a skip", review)

    def test_review_step_mentions_base_flag_and_project_context(self):
        content = _build_messages(_make_task())[1]
        self.assertIn("--base", content)
        self.assertIn("-c CLAUDE.md", content)

    def test_second_message_inverts_the_note_cadence(self):
        # #901: the old text said "prefer several small notes", which produced
        # 13 client-visible chatter messages in one 30-minute run. Checkpoints
        # are now local (interim=True) and exactly ONE note is posted at STOP.
        content = _build_messages(_make_task())[1]
        self.assertIn("after each coherent", content)
        self.assertIn("interim=True", content)
        self.assertIn("local session log", content)
        self.assertIn("consolidated", content)
        self.assertNotIn("Prefer several small notes", content)

    def test_plan_note_is_local_not_posted_to_chatter(self):
        # #901: step 2 used to post the plan to chatter as its own message.
        content = _build_messages(_make_task())[1]
        analyze = content[content.index("**ANALYZE**") : content.index("**IMPLEMENT**")]
        self.assertIn(
            'task_note(42, "Implementation plan: ...", interim=True)', analyze
        )

    def test_interim_chatter_note_is_the_exception_not_the_cadence(self):
        # #901: a posted mid-run note is allowed only when blocked or when the
        # run is long enough that silence is worse than the notification.
        content = _build_messages(_make_task())[1]
        implement = content[content.index("**IMPLEMENT**") : content.index("**TEST**")]
        self.assertIn("ONLY as an exception", implement)
        self.assertIn("blocked", implement)
        self.assertIn("long-running", implement)
        self.assertIn("NEVER one note per file-group", implement)

    def test_stop_step_posts_one_consolidated_note(self):
        # #901: the STOP step is where the single client-visible note is made,
        # and it must name the four things that note has to carry.
        content = _build_messages(_make_task())[1]
        stop = content[content.index("**STOP**") :]
        self.assertIn("ONE consolidated chatter note", stop)
        self.assertIn("what changed", stop)
        self.assertIn("tests you ran", stop)
        self.assertIn("review outcome", stop)
        self.assertIn("PR link", stop)

    def test_note_style_allows_the_full_chatter_budget(self):
        # #901: with one note per run, the "several small notes" guidance is
        # gone and the note may spend the whole (raised) cap.
        content = _build_messages(_make_task())[1]
        style = content[
            content.index("## Note Style") : content.index("## Tool Reference")
        ]
        self.assertIn("One consolidated note per run", style)
        self.assertIn(str(MAX_CHATTER_BODY_CHARS), style)
        self.assertNotIn("Prefer several small notes", style)

    def test_tool_reference_distinguishes_interim_from_posted_notes(self):
        # #901: the table is what an agent reads when deciding how to call the
        # tool, so the two modes must be told apart there.
        content = _build_messages(_make_task())[1]
        table = content[
            content.index("## Tool Reference") : content.index("## Guard Conditions")
        ]
        row = next(line for line in table.splitlines() if "`task_note`" in line)
        self.assertIn("interim=True", row)
        self.assertIn("local session log only", row)
        self.assertIn("client-visible", row)
        self.assertIn(str(MAX_CHATTER_BODY_CHARS), row)

    def test_tool_reference_names_the_artifacts_dir_and_next_stage(self):
        # #784 part (b): the workflow ended at STOP with no pointer to where
        # evidence is recorded or which stage runs next, so work started here
        # could never reach /odoo-dev:pr.
        content = _build_messages(_make_task())[1]
        table = content[
            content.index("## Tool Reference") : content.index("## Guard Conditions")
        ]
        self.assertIn("artifacts directory", table)
        self.assertIn("plugins/odoo-dev/scripts/artifact.sh", table)
        self.assertIn("30-test.json", table)
        self.assertIn("/odoo-dev:pr", table)

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
        return next(
            p for p in captured if isinstance(p, Prompt) and p.name == "report_incident"
        )

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
                "implement_task",
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
                "implement_task",
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


if __name__ == "__main__":
    unittest.main()
