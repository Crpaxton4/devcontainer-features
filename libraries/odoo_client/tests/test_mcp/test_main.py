import unittest
from unittest.mock import MagicMock, patch

from odoo_client.commands.builtin import BUILTIN_COMMANDS
from odoo_client.mcp import __main__ as entry
from odoo_client.mcp.tools import TOOL_FACTORIES


class TestMainEntryPoint(unittest.TestCase):
    def setUp(self):
        guard = patch("odoo_client.mcp.__main__.assert_configured")
        self.guard = guard.start()
        self.addCleanup(guard.stop)

    def test_main_builds_default_registry_and_runs(self):
        with (
            patch("odoo_client.mcp.__main__.OdooClient") as MockClient,
            patch("odoo_client.mcp.__main__.OdooMCPServer") as MockServer,
        ):
            entry.main()

        MockClient.assert_called_once_with()
        MockServer.assert_called_once()
        MockServer.return_value.run.assert_called_once_with()

    def test_main_checks_the_configuration_first(self):
        with (
            patch("odoo_client.mcp.__main__.OdooClient"),
            patch("odoo_client.mcp.__main__.OdooMCPServer"),
        ):
            entry.main()

        self.guard.assert_called_once_with()

    def test_main_registers_all_builtins(self):
        with (
            patch("odoo_client.mcp.__main__.OdooClient"),
            patch("odoo_client.mcp.__main__.OdooMCPServer") as MockServer,
        ):
            entry.main()

        registry = MockServer.call_args.args[0]
        registered = [name for name, _ in registry.items()]
        self.assertEqual(set(registered), set(BUILTIN_COMMANDS))

    def test_main_passes_resolved_profiling_to_server(self):
        fake_config = MagicMock()
        fake_config.profiling = True
        with (
            patch("odoo_client.mcp.__main__.OdooClient"),
            patch("odoo_client.mcp.__main__.LocalConfig") as MockConfig,
            patch("odoo_client.mcp.__main__.OdooMCPServer") as MockServer,
        ):
            MockConfig.load.return_value = fake_config
            entry.main()

        self.assertIs(MockServer.call_args.kwargs["profiling"], True)

    def test_main_exposes_every_tool(self):
        # There is no opt-in gate: the server is handed the whole tool surface.
        with (
            patch("odoo_client.mcp.__main__.OdooClient"),
            patch("odoo_client.mcp.__main__.OdooMCPServer") as MockServer,
        ):
            entry.main()

        exposed = set(MockServer.call_args.kwargs["explicit_tools"])
        self.assertEqual(exposed, set(TOOL_FACTORIES))


if __name__ == "__main__":
    unittest.main()
