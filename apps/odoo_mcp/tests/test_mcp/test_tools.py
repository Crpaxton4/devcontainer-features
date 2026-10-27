"""Tests for the explicit MCP tool factories and their wire schemas."""

import unittest
from unittest.mock import MagicMock, patch

from odoo_mcp.tools import build_explicit_tools


class TestBuildExplicitTools(unittest.TestCase):
    def test_builds_full_tool_surface(self):
        from odoo_dev_core.commands import Registry
        from odoo_dev_core.commands.builtin import BUILTIN_COMMANDS, register_builtins

        registry = register_builtins(Registry(MagicMock()))
        tools = build_explicit_tools(registry)
        # The MCP surface is a subset of the builtin surface, not a mirror of it:
        # MCP names its tools explicitly, so a builtin the LLM has no use for
        # (``get_employee_id``, used by the unattended export path) registers as
        # a command without becoming a tool (#499).
        self.assertLessEqual(set(tools), set(BUILTIN_COMMANDS))
        self.assertNotIn("get_employee_id", tools)
        # Every tool carries a non-empty description sourced from its command,
        # so no tool ships to the MCP wire schema without documentation.
        for name, (_, description) in tools.items():
            self.assertNotEqual(description, "", f"{name} has an empty description")
        # Each entry is a (callable, description) pair.
        for fn, _ in tools.values():
            self.assertTrue(callable(fn))

    def test_description_empty_when_command_missing(self):
        class _EmptyRegistry:
            def __getitem__(self, name):
                raise KeyError(name)

        tools = build_explicit_tools(_EmptyRegistry())
        # Tools are still built; descriptions default to empty string.
        _, desc = tools["get_uid"]
        self.assertEqual(desc, "")


class TestAtomicToolInvocation(unittest.TestCase):
    """Each atomic tool delegates to its like-named command's execute()."""

    def _registry(self):
        class _Reg:
            def __getitem__(self, name):
                cmd = MagicMock()
                cmd.execute.side_effect = lambda *a, **k: f"{name}-result"
                cmd.description = f"{name} description"
                return cmd

        return _Reg()

    def test_all_atomic_tools_route_to_command(self):
        from odoo_mcp.tools.atomic import ATOMIC_TOOL_FACTORIES

        calls = {
            "get_uid": (),
            "get_models": (),
            "get_tasks": (),
            "get_todo": (5,),
            "get_task": (5,),
            "get_task_chatter": (5,),
            "get_mail_status": ("project.task", 5),
            "get_task_attachments": (5,),
            "read_attachment": (5,),
            "create_task": ("n", 1, "d"),
            "search_projects": ("q",),
            "search_tasks": ("q", 1),
            "assign_event": ([5], 1),
            "normalize_timesheets": (),
            "schedule_activity": (5,),
            "get_activities": (),
            "mark_activity_done": (5,),
            "search_activity_types": (),
            "search_chatter": ("q",),
            "search_count": ("project.task",),
            "search_knowledge_articles": ("q",),
            "read_knowledge_article": (5,),
            "task_note": (5, "note"),
            "task_list": (),
            "task_aging": (),
            "task_question": (5, "q?"),
            "optimize_sessions": (),
            "query_sessions": (),
            "resync": (),
            "timesheet_summary": ("2026-07-01", "2026-07-31"),
            "unbilled_hours": (),
            "unlogged_time_report": ("2026-07-01", "2026-07-15"),
        }
        # Pin the atomic tool set to this explicit map so a dropped or renamed
        # @atomic_tool decorator fails here instead of silently going untested.
        self.assertEqual(set(ATOMIC_TOOL_FACTORIES), set(calls))
        for name, factory in ATOMIC_TOOL_FACTORIES.items():
            tool = factory(self._registry())
            result = tool(*calls[name])
            self.assertEqual(result, f"{name}-result")


class TestGetTaskToolSchema(unittest.TestCase):
    """Introspect the get_task tool's wire schema as the server builds it."""

    def _tool(self):
        from fastmcp.tools import Tool

        from odoo_mcp.tools.atomic import make_get_task_tool

        class _Reg:
            def __getitem__(self, name):
                cmd = MagicMock()
                cmd.execute.side_effect = lambda *a, **k: {"task_id": a[0]}
                return cmd

        fn = make_get_task_tool(_Reg())
        return Tool.from_function(fn, name="get_task")

    def test_include_selector_in_input_schema(self):
        schema = self._tool().parameters
        self.assertIn("include", schema["properties"])

    def test_task_id_only_call_still_valid(self):
        # ``task_id`` is the sole required property, so a task_id-only call is
        # schema-valid (backwards compatibility).
        schema = self._tool().parameters
        self.assertEqual(schema["required"], ["task_id"])
        self.assertIn("task_id", schema["properties"])

    def test_include_defaults_to_none(self):
        schema = self._tool().parameters
        self.assertEqual(schema["properties"]["include"].get("default"), None)

    def test_task_id_only_invocation_routes(self):
        fn = make_get_task_tool_reg()
        self.assertEqual(fn(5), {"task_id": 5})


def make_get_task_tool_reg():
    from odoo_mcp.tools.atomic import make_get_task_tool

    class _Reg:
        def __getitem__(self, name):
            cmd = MagicMock()
            cmd.execute.side_effect = lambda *a, **k: {"task_id": a[0]}
            return cmd

    return make_get_task_tool(_Reg())


class TestGetTasksToolSchema(unittest.TestCase):
    """The get_tasks tool's wire schema and include passthrough (#630)."""

    def _make(self):
        from odoo_mcp.tools.atomic import make_get_tasks_tool

        class _Reg:
            def __getitem__(self, name):
                cmd = MagicMock()
                cmd.execute.side_effect = lambda *a, **k: {"args": a, "kwargs": k}
                return cmd

        return make_get_tasks_tool(_Reg())

    def _schema(self):
        from fastmcp.tools import Tool

        return Tool.from_function(self._make(), name="get_tasks").parameters

    def test_include_selector_in_input_schema(self):
        self.assertIn("include", self._schema()["properties"])

    def test_include_optional_and_defaults_to_none(self):
        schema = self._schema()
        # No property becomes required: a bare get_tasks() call stays
        # schema-valid (backwards compatibility).
        self.assertEqual(schema.get("required", []), [])
        self.assertEqual(schema["properties"]["include"].get("default"), None)

    def test_include_forwarded_to_command(self):
        fn = self._make()
        domain = [("stage_id", "=", 3)]
        result = fn(domain=domain, limit=5, include=["description"])
        self.assertEqual(
            result["kwargs"],
            {"domain": domain, "limit": 5, "include": ["description"]},
        )

    def test_plain_call_forwards_none_include(self):
        # Omitted ``include`` reaches the command as None, keeping the
        # summary-only behavior unchanged.
        fn = self._make()
        result = fn()
        self.assertEqual(
            result["kwargs"], {"domain": None, "limit": 10, "include": None}
        )


class TestTaskNoteToolSchema(unittest.TestCase):
    """The task_note tool's wire schema after #604 (attachments) and #610 (cap)."""

    def _make(self):
        from odoo_mcp.tools.atomic import make_task_note_tool

        class _Reg:
            def __getitem__(self, name):
                cmd = MagicMock()
                cmd.execute.side_effect = lambda *a, **k: {"args": a, "kwargs": k}
                return cmd

        return make_task_note_tool(_Reg())

    def _schema(self):
        from fastmcp.tools import Tool

        return Tool.from_function(self._make(), name="task_note").parameters

    def test_attachments_in_input_schema(self):
        self.assertIn("attachments", self._schema()["properties"])

    def test_attachments_optional_and_defaults_to_none(self):
        schema = self._schema()
        # ``task_id`` and ``note`` stay the only required properties, so an
        # attachment-less call remains schema-valid (backwards compatibility).
        self.assertEqual(schema["required"], ["task_id", "note"])
        self.assertEqual(schema["properties"]["attachments"].get("default"), None)

    def test_dedupe_key_optional_and_defaults_to_none(self):
        # #631: the idempotency key is opt-in and never required.
        schema = self._schema()
        self.assertIn("dedupe_key", schema["properties"])
        self.assertEqual(schema["required"], ["task_id", "note"])
        self.assertEqual(schema["properties"]["dedupe_key"].get("default"), None)

    def test_docstring_names_the_500_char_limit(self):
        # MCP callers must see the #610 cap up front, in the tool itself as
        # well as in the command-sourced description. #901 raised it to 500.
        self.assertIn("500", self._make().__doc__)
        self.assertNotIn("300", self._make().__doc__)

    def test_docstring_states_the_attachment_audience(self):
        # #767: this docstring is the ONLY surface an MCP client sees when no
        # prompt is loaded, so the client-visible warning must live here too
        # rather than only in the implement_task workflow.
        doc = self._make().__doc__
        self.assertIn("CLIENT-VISIBLE", doc)
        self.assertIn("deliverables the client asked", doc)
        self.assertIn("scripts,", doc)

    def test_attachments_forwarded_to_command(self):
        fn = self._make()
        specs = [{"path": "/tmp/report.csv"}]
        result = fn(5, "note", specs)
        self.assertEqual(result["args"], (5, "note"))
        self.assertEqual(
            result["kwargs"],
            {"attachments": specs, "dedupe_key": None},
        )

    def test_dedupe_key_forwarded_to_command(self):
        fn = self._make()
        result = fn(5, "note", None, "note-abc")
        self.assertEqual(
            result["kwargs"],
            {"attachments": None, "dedupe_key": "note-abc"},
        )

    def test_plain_call_forwards_none_attachments(self):
        fn = self._make()
        result = fn(5, "note")
        self.assertEqual(
            result["kwargs"],
            {"attachments": None, "dedupe_key": None},
        )


class TestGetTaskChatterToolSchema(unittest.TestCase):
    """The get_task_chatter tool's wire schema after the #624 since-cursor."""

    def _make(self):
        from odoo_mcp.tools.atomic import make_get_task_chatter_tool

        class _Reg:
            def __getitem__(self, name):
                cmd = MagicMock()
                cmd.execute.side_effect = lambda *a, **k: {"args": a, "kwargs": k}
                return cmd

        return make_get_task_chatter_tool(_Reg())

    def _schema(self):
        from fastmcp.tools import Tool

        return Tool.from_function(self._make(), name="get_task_chatter").parameters

    def test_since_optional_and_defaults_to_none(self):
        schema = self._schema()
        self.assertIn("since", schema["properties"])
        # ``task_id`` stays the sole required property (backwards compat).
        self.assertEqual(schema["required"], ["task_id"])
        self.assertEqual(schema["properties"]["since"].get("default"), None)

    def test_since_forwarded_to_command(self):
        fn = self._make()
        result = fn(5, 20, 77)
        self.assertEqual(result["args"], (5,))
        self.assertEqual(result["kwargs"], {"limit": 20, "since": 77})

    def test_plain_call_forwards_none_since(self):
        fn = self._make()
        result = fn(5)
        self.assertEqual(result["kwargs"], {"limit": 100, "since": None})


class TestTaskQuestionToolSchema(unittest.TestCase):
    """The task_question tool's wire schema after the #631 dedupe key."""

    def _make(self):
        from odoo_mcp.tools.atomic import make_task_question_tool

        class _Reg:
            def __getitem__(self, name):
                cmd = MagicMock()
                cmd.execute.side_effect = lambda *a, **k: {"args": a, "kwargs": k}
                return cmd

        return make_task_question_tool(_Reg())

    def _schema(self):
        from fastmcp.tools import Tool

        return Tool.from_function(self._make(), name="task_question").parameters

    def test_dedupe_key_optional_and_defaults_to_none(self):
        schema = self._schema()
        self.assertIn("dedupe_key", schema["properties"])
        self.assertEqual(schema["required"], ["task_id", "question"])
        self.assertEqual(schema["properties"]["dedupe_key"].get("default"), None)

    def test_dedupe_key_forwarded_to_command(self):
        fn = self._make()
        result = fn(5, "q?", "q-abc")
        self.assertEqual(result["args"], (5, "q?"))
        self.assertEqual(result["kwargs"], {"dedupe_key": "q-abc"})

    def test_plain_call_forwards_none_dedupe_key(self):
        fn = self._make()
        result = fn(5, "q?")
        self.assertEqual(result["kwargs"], {"dedupe_key": None})


class TestAtomicToolDecorator(unittest.TestCase):
    """``@atomic_tool("name")`` populates ``ATOMIC_TOOL_FACTORIES``."""

    def test_registers_factory_under_explicit_name(self):
        from odoo_mcp.tools.atomic import ATOMIC_TOOL_FACTORIES, atomic_tool

        def _factory(registry):  # pragma: no cover - never invoked
            return lambda: None

        # patch.dict restores ATOMIC_TOOL_FACTORIES after the block so the probe
        # registration never leaks into the real atomic tool surface.
        with patch.dict(ATOMIC_TOOL_FACTORIES, clear=False):
            returned = atomic_tool("probe_tool")(_factory)
            # The decorator is transparent and keys by the explicit name.
            self.assertIs(returned, _factory)
            self.assertIs(ATOMIC_TOOL_FACTORIES["probe_tool"], _factory)
        self.assertNotIn("probe_tool", ATOMIC_TOOL_FACTORIES)

    def test_name_decouples_from_command_name(self):
        # The explicit tool name may differ from the command the body looks up.
        from odoo_mcp.tools.atomic import ATOMIC_TOOL_FACTORIES, atomic_tool

        def _factory(registry):  # pragma: no cover - never invoked
            return lambda: registry["backing_command"].execute()

        with patch.dict(ATOMIC_TOOL_FACTORIES, clear=False):
            atomic_tool("public_alias")(_factory)
            self.assertIn("public_alias", ATOMIC_TOOL_FACTORIES)
            self.assertNotIn("backing_command", ATOMIC_TOOL_FACTORIES)

    def test_duplicate_name_raises(self):
        from odoo_mcp.tools.atomic import ATOMIC_TOOL_FACTORIES, atomic_tool

        def _factory(registry):  # pragma: no cover - never invoked
            return lambda: None

        original = ATOMIC_TOOL_FACTORIES["get_uid"]
        with self.assertRaises(ValueError) as ctx:
            atomic_tool("get_uid")(_factory)
        self.assertIn("get_uid", str(ctx.exception))
        # The collision left the genuine factory in place (no silent overwrite).
        self.assertIs(ATOMIC_TOOL_FACTORIES["get_uid"], original)
