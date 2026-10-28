"""Tests for the ``odoo-tui`` console entry point.

The entry point bootstraps the default command service from a throwaway state
home and a configured environment, then hands it to the Textual driver. The
driver itself is patched out so the test never opens a terminal.
"""

import os
import tempfile
import unittest
from unittest.mock import patch

from odoo_dev_core import default_registry
from odoo_tui import __main__ as entry

_ENV = {
    "ODOO_URL": "https://odoo.example.com",
    "ODOO_DB": "db",
    "ODOO_USERNAME": "user@example.com",
    "ODOO_PASSWORD": "secret",
    "ODOO_API_KEY": "key",
}


class TestMainEntryPoint(unittest.TestCase):
    def setUp(self):
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        env = patch.dict(os.environ, {**_ENV, "ODOO_DEV_HOME": home.name})
        env.start()
        self.addCleanup(env.stop)

    def test_main_checks_the_configuration_first(self):
        with (
            patch.dict(os.environ, {"ODOO_URL": ""}),
            patch("odoo_tui.__main__.run") as mock_run,
        ):
            with self.assertRaises(ValueError):
                entry.main()

        mock_run.assert_not_called()

    def test_main_builds_default_service_and_runs(self):
        with patch("odoo_tui.__main__.run") as mock_run:
            entry.main()

        mock_run.assert_called_once()

    def test_main_registers_all_builtins(self):
        with patch("odoo_tui.__main__.run") as mock_run:
            entry.main()

        # The driver receives an injected ``TuiDeps`` bundle; its service
        # carries every built-in command.
        deps = mock_run.call_args.args[0]
        self.assertEqual(deps.service.registry.ids(), default_registry().ids())


if __name__ == "__main__":
    unittest.main()
