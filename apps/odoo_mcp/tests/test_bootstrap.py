"""Smoke test of the ``odoo_mcp`` composition root."""

import unittest
from unittest.mock import MagicMock

from odoo_dev_data.config import LocalConfig

from odoo_dev_core.commands import Registry
from odoo_dev_core.commands.builtin import BUILTIN_COMMANDS
from odoo_mcp.bootstrap import bootstrap


class TestBootstrapSmoke(unittest.TestCase):
    def test_bootstrap_with_fake_client_registers_the_builtins(self):
        registry = bootstrap(client=MagicMock(), config=LocalConfig(connection={}))

        self.assertIsInstance(registry, Registry)
        registered = {name for name, _ in registry.items()}
        self.assertEqual(registered, set(BUILTIN_COMMANDS))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
