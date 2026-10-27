import unittest
from unittest.mock import MagicMock, patch

from odoo_dev_core import default_registry
from odoo_mcp import __main__ as entry
from odoo_mcp.tools import TOOL_FACTORIES


class TestMainEntryPoint(unittest.TestCase):
    def setUp(self):
        guard = patch("odoo_mcp.__main__.assert_configured")
        self.guard = guard.start()
        self.addCleanup(guard.stop)

    def test_main_builds_default_service_and_runs(self):
        with (
            patch("odoo_mcp.bootstrap.build_client") as MockClient,
            patch("odoo_mcp.__main__.OdooMCPServer") as MockServer,
        ):
            entry.main()

        MockClient.assert_called_once()
        MockServer.assert_called_once()
        MockServer.return_value.run.assert_called_once_with()

    def test_main_checks_the_configuration_first(self):
        with (
            patch("odoo_mcp.bootstrap.build_client"),
            patch("odoo_mcp.__main__.OdooMCPServer"),
        ):
            entry.main()

        self.guard.assert_called_once_with()

    def test_main_registers_all_builtins(self):
        with (
            patch("odoo_mcp.bootstrap.build_client"),
            patch("odoo_mcp.__main__.OdooMCPServer") as MockServer,
        ):
            entry.main()

        service = MockServer.call_args.args[0]
        self.assertEqual(service.registry.ids(), default_registry().ids())

    def test_main_passes_resolved_profiling_to_server(self):
        fake_config = MagicMock()
        fake_config.profiling = True
        with (
            patch("odoo_mcp.bootstrap.build_client"),
            patch("odoo_mcp.__main__.LocalConfig") as MockConfig,
            patch("odoo_mcp.__main__.OdooMCPServer") as MockServer,
        ):
            MockConfig.load.return_value = fake_config
            entry.main()

        self.assertIs(MockServer.call_args.kwargs["profiling"], True)

    def test_main_exposes_every_tool(self):
        # There is no opt-in gate: the server is handed the whole tool surface.
        with (
            patch("odoo_mcp.bootstrap.build_client"),
            patch("odoo_mcp.__main__.OdooMCPServer") as MockServer,
        ):
            entry.main()

        exposed = set(MockServer.call_args.kwargs["explicit_tools"])
        self.assertEqual(exposed, set(TOOL_FACTORIES))


if __name__ == "__main__":
    unittest.main()
