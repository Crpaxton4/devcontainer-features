import unittest
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
