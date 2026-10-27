"""The tool surface generated from ``registry.specs("mcp")``."""

import dataclasses
import inspect
import typing
import unittest
from unittest.mock import Mock

from fastmcp.tools import Tool
from odoo_mcp.tools import build_tools

from ..support import make_service

NON_MCP = {"get_employee_id", "log_event", "upload"}


def _fields(spec):
    return {f.name: f for f in dataclasses.fields(spec.args)}


class TestGeneratedTools(unittest.TestCase):
    def setUp(self):
        self.service = make_service(client=Mock())
        self.specs = self.service.registry.specs("mcp")
        self.tools = build_tools(self.service)

    def test_one_tool_per_mcp_spec(self):
        self.assertEqual(len(self.tools), 32)
        self.assertEqual(set(self.tools), {spec.id for spec in self.specs})

    def test_non_mcp_commands_are_absent(self):
        self.assertFalse(set(self.tools) & NON_MCP)
        self.assertTrue(NON_MCP <= set(self.service.registry.ids()))

    def test_name_doc_and_description_come_from_the_spec(self):
        for spec in self.specs:
            fn, description = self.tools[spec.id]
            self.assertEqual(fn.__name__, spec.id)
            self.assertEqual(fn.__qualname__, spec.id)
            self.assertEqual(fn.__doc__, spec.description)
            self.assertEqual(description, spec.description)

    def test_signature_parameters_equal_the_args_fields(self):
        for spec in self.specs:
            params = inspect.signature(self.tools[spec.id][0]).parameters
            fields = _fields(spec)
            self.assertEqual(list(params), list(fields), spec.id)
            hints = typing.get_type_hints(spec.args)
            for name, param in params.items():
                self.assertIs(param.kind, inspect.Parameter.KEYWORD_ONLY)
                self.assertEqual(param.annotation, hints[name])
                field = fields[name]
                if field.default is not dataclasses.MISSING:
                    self.assertEqual(param.default, field.default)
                elif field.default_factory is not dataclasses.MISSING:
                    self.assertEqual(param.default, field.default_factory())
                else:
                    self.assertIs(param.default, inspect.Parameter.empty)

    def test_fastmcp_schema_matches_the_args_fields(self):
        for spec in self.specs:
            fn, description = self.tools[spec.id]
            tool = Tool.from_function(fn, name=spec.id, description=description)
            fields = _fields(spec)
            self.assertEqual(set(tool.parameters["properties"]), set(fields), spec.id)
            required = [
                name
                for name, f in fields.items()
                if f.default is dataclasses.MISSING
                and f.default_factory is dataclasses.MISSING
            ]
            self.assertEqual(tool.parameters.get("required", []), required, spec.id)

    def test_calling_a_tool_executes_the_command_with_the_kwargs(self):
        calls = []
        self.service.execute = lambda id, /, **kw: calls.append((id, kw)) or "done"
        fn, _ = build_tools(self.service)["task_note"]
        self.assertEqual(fn(task_id=3, note="hi"), "done")
        self.assertEqual(calls, [("task_note", {"task_id": 3, "note": "hi"})])

    def test_calling_a_tool_runs_the_handler(self):
        client = Mock()
        client.uid = 7
        fn, _ = build_tools(make_service(client=client))["get_uid"]
        self.assertEqual(fn(), 7)


if __name__ == "__main__":
    unittest.main()
