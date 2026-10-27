"""Tests for the ``odoo-tui`` console entry point.

The entry point wires the default registry and hands it to the Textual driver;
the driver itself is patched out so the test never opens a terminal. This mirrors
the MCP ``__main__`` entry-point test and covers the module's imports.
"""

import unittest
from unittest.mock import patch

from odoo_client.commands.builtin import BUILTIN_COMMANDS
from odoo_client.tui import __main__ as entry


class TestMainEntryPoint(unittest.TestCase):
    def setUp(self):
        guard = patch("odoo_client.tui.__main__.assert_configured")
        self.guard = guard.start()
        self.addCleanup(guard.stop)

    def test_main_checks_the_configuration_first(self):
        with (
            patch("odoo_client.tui.__main__.OdooClient"),
            patch("odoo_client.tui.__main__.run"),
            patch("odoo_client.tui.__main__.LocalConfig"),
        ):
            entry.main()

        self.guard.assert_called_once_with()

    def test_main_builds_default_registry_and_runs(self):
        with (
            patch("odoo_client.tui.__main__.OdooClient") as MockClient,
            patch("odoo_client.tui.__main__.run") as mock_run,
            patch("odoo_client.tui.__main__.LocalConfig"),
        ):
            entry.main()

        MockClient.assert_called_once_with()
        mock_run.assert_called_once()

    def test_main_registers_all_builtins(self):
        with (
            patch("odoo_client.tui.__main__.OdooClient"),
            patch("odoo_client.tui.__main__.run") as mock_run,
            patch("odoo_client.tui.__main__.LocalConfig"),
        ):
            entry.main()

        # The driver now receives an injected ``TuiDeps`` bundle; its registry
        # carries every built-in command.
        deps = mock_run.call_args.args[0]
        registered = [name for name, _ in deps.registry.items()]
        self.assertEqual(set(registered), set(BUILTIN_COMMANDS))


if __name__ == "__main__":
    unittest.main()
