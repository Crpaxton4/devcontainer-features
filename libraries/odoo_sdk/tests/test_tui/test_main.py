"""Tests for the ``odoo-tui`` console entry point.

The entry point wires the default registry and hands it to the curses driver; the
driver itself is patched out so the test never opens a terminal. This mirrors the
MCP ``__main__`` entry-point test and covers the module's imports.
"""

import unittest
from unittest.mock import patch

from odoo_sdk.commands.builtin import BUILTIN_COMMANDS
from odoo_sdk.tui import __main__ as entry


class TestMainEntryPoint(unittest.TestCase):
    def test_main_builds_default_registry_and_runs(self):
        with (
            patch("odoo_sdk.tui.__main__.OdooClient") as MockClient,
            patch("odoo_sdk.tui.__main__.run") as mock_run,
            patch("odoo_sdk.tui.__main__.LocalConfig"),
        ):
            entry.main()

        MockClient.assert_called_once_with()
        mock_run.assert_called_once()

    def test_main_registers_all_builtins(self):
        with (
            patch("odoo_sdk.tui.__main__.OdooClient"),
            patch("odoo_sdk.tui.__main__.run") as mock_run,
            patch("odoo_sdk.tui.__main__.LocalConfig"),
        ):
            entry.main()

        registry = mock_run.call_args.args[0]
        registered = [name for name, _ in registry.items()]
        self.assertEqual(set(registered), set(BUILTIN_COMMANDS))


if __name__ == "__main__":
    unittest.main()
