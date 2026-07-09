"""Regression tests: the injected ``ctx`` param must not leak into tool schemas.

FastMCP auto-injects a Context parameter and excludes it from the generated
input JSON schema only when that parameter is annotated with its ``Context``
type. Annotating ``ctx`` as ``Any`` makes ``ctx`` wrongly appear in the tool's
input schema (and marks it required), so it is never auto-injected.

These tests reproduce the exact way :class:`OdooMCPServer` builds tools — the
explicit-tools path through ``Tool.from_function`` with the same
``_toon_encoded``/``_profiled`` wrappers — and assert ``ctx`` is absent from
both ``properties`` and ``required`` of the generated schema. They fail on the
old ``ctx: Any`` and pass with ``ctx: Context``.
"""

import unittest
from unittest.mock import MagicMock

from fastmcp.tools.tool import Tool

from odoo_sdk.mcp.server import _profiled, _toon_encoded
from odoo_sdk.mcp.tools.start_task import make_start_task_tool
from odoo_sdk.mcp.tools.stop_task import make_stop_task_tool


def _build_tool(tool_fn, name):
    """Build a Tool exactly as ``OdooMCPServer._register_tools`` does.

    Applies both server wrappers (profiling on, to exercise the deepest wrapper
    stack) before constructing the Tool via ``Tool.from_function``.
    """
    tool_fn = _toon_encoded(tool_fn)
    tool_fn = _profiled(tool_fn, name)
    return Tool.from_function(tool_fn, name=name)


class TestCtxNotInSchema(unittest.TestCase):
    def _assert_ctx_absent(self, tool_fn, name):
        tool = _build_tool(tool_fn, name)
        schema = tool.parameters
        self.assertNotIn(
            "ctx",
            schema.get("properties", {}),
            f"{name}: ctx leaked into input schema properties",
        )
        self.assertNotIn(
            "ctx",
            schema.get("required", []),
            f"{name}: ctx wrongly marked required in input schema",
        )

    def test_start_task_ctx_not_in_schema(self):
        self._assert_ctx_absent(make_start_task_tool(MagicMock()), "start_task")

    def test_stop_task_ctx_not_in_schema(self):
        self._assert_ctx_absent(make_stop_task_tool(MagicMock()), "stop_task")


if __name__ == "__main__":
    unittest.main()
