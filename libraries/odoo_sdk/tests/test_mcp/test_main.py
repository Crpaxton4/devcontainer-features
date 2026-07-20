import os
import unittest
from unittest.mock import MagicMock, patch

from odoo_sdk.commands.builtin import BUILTIN_COMMANDS
from odoo_sdk.mcp import __main__ as entry
from odoo_sdk.mcp.tools import GATED_TOOL_NAMES, GATED_TOOLS_ENV


class TestMainEntryPoint(unittest.TestCase):
    def test_main_builds_default_registry_and_runs(self):
        with (
            patch("odoo_sdk.mcp.__main__.OdooClient") as MockClient,
            patch("odoo_sdk.mcp.__main__.OdooMCPServer") as MockServer,
        ):
            entry.main()

        MockClient.assert_called_once_with()
        MockServer.assert_called_once()
        MockServer.return_value.run.assert_called_once_with()

    def test_main_registers_all_builtins(self):
        with (
            patch("odoo_sdk.mcp.__main__.OdooClient"),
            patch("odoo_sdk.mcp.__main__.OdooMCPServer") as MockServer,
        ):
            entry.main()

        registry = MockServer.call_args.args[0]
        registered = [name for name, _ in registry.items()]
        self.assertEqual(set(registered), set(BUILTIN_COMMANDS))

    def test_main_passes_resolved_profiling_to_server(self):
        fake_config = MagicMock()
        fake_config.profiling = True
        with (
            patch("odoo_sdk.mcp.__main__.OdooClient"),
            patch("odoo_sdk.mcp.__main__.LocalConfig") as MockConfig,
            patch("odoo_sdk.mcp.__main__.OdooMCPServer") as MockServer,
        ):
            MockConfig.load.return_value = fake_config
            entry.main()

        self.assertIs(MockServer.call_args.kwargs["profiling"], True)

    def test_main_exposes_reduced_default_surface(self):
        # The server is handed the everyday working set, not all 39 tools: the
        # gated (maintenance/triage/introspection) tools are held back so the
        # count stays under the lazy-deferral threshold (#512).
        with (
            patch("odoo_sdk.mcp.__main__.OdooClient"),
            patch("odoo_sdk.mcp.__main__.OdooMCPServer") as MockServer,
            # Empty (falsy) value keeps the gate closed without clearing the rest
            # of the environment the entry point's config load depends on.
            patch.dict(os.environ, {GATED_TOOLS_ENV: ""}),
        ):
            entry.main()

        exposed = set(MockServer.call_args.kwargs["explicit_tools"])
        self.assertEqual(exposed & GATED_TOOL_NAMES, set())
        self.assertLess(len(exposed), 39)
        # The composition tools remain reachable on the default surface.
        self.assertIn("start_task", exposed)
        self.assertIn("stop_task", exposed)

    def test_main_env_opt_in_exposes_the_gated_tools(self):
        # Opting in restores the full surface for a session that needs the
        # maintenance/triage tooling.
        with (
            patch("odoo_sdk.mcp.__main__.OdooClient"),
            patch("odoo_sdk.mcp.__main__.OdooMCPServer") as MockServer,
            patch.dict(os.environ, {GATED_TOOLS_ENV: "1"}),
        ):
            entry.main()

        exposed = set(MockServer.call_args.kwargs["explicit_tools"])
        self.assertLessEqual(GATED_TOOL_NAMES, exposed)


if __name__ == "__main__":
    unittest.main()
