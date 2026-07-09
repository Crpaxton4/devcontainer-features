import unittest
from unittest.mock import MagicMock, patch

from odoo_sdk.commands.builtin import BUILTIN_COMMANDS
from odoo_sdk.mcp import __main__ as entry


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


if __name__ == "__main__":
    unittest.main()
