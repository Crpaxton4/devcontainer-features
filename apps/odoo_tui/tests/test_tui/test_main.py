"""Tests for the ``odoo-tui`` console entry point.

The entry point wires the default command service and hands it to the Textual driver;
the driver itself is patched out so the test never opens a terminal. This mirrors
the MCP ``__main__`` entry-point test and covers the module's imports.
"""

import unittest
from unittest.mock import patch

from odoo_dev_core import default_registry
from odoo_tui import __main__ as entry


class TestMainEntryPoint(unittest.TestCase):
    def setUp(self):
        guard = patch("odoo_tui.__main__.assert_configured")
        self.guard = guard.start()
        self.addCleanup(guard.stop)

    def test_main_checks_the_configuration_first(self):
        with (
            patch("odoo_tui.__main__.build_client"),
            patch("odoo_tui.__main__.run"),
            patch("odoo_tui.__main__.LocalConfig"),
        ):
            entry.main()

        self.guard.assert_called_once_with()

    def test_main_builds_default_service_and_runs(self):
        with (
            patch("odoo_tui.__main__.build_client") as MockClient,
            patch("odoo_tui.__main__.run") as mock_run,
            patch("odoo_tui.__main__.LocalConfig"),
        ):
            entry.main()

        MockClient.assert_called_once()
        mock_run.assert_called_once()

    def test_main_registers_all_builtins(self):
        with (
            patch("odoo_tui.__main__.build_client"),
            patch("odoo_tui.__main__.run") as mock_run,
            patch("odoo_tui.__main__.LocalConfig"),
        ):
            entry.main()

        # The driver receives an injected ``TuiDeps`` bundle; its service
        # carries every built-in command.
        deps = mock_run.call_args.args[0]
        self.assertEqual(deps.service.registry.ids(), default_registry().ids())


if __name__ == "__main__":
    unittest.main()
