"""Smoke test of the ``odoo_mcp`` composition root."""

import unittest
from unittest.mock import MagicMock

from odoo_dev_core import CommandService, default_registry
from odoo_dev_data.config import LocalConfig

from odoo_mcp.bootstrap import bootstrap


class TestBootstrapSmoke(unittest.TestCase):
    def test_bootstrap_with_fake_client_wires_the_builtins(self):
        service = bootstrap(
            client=MagicMock(), config=LocalConfig(connection={}), env={}
        )

        self.assertIsInstance(service, CommandService)
        self.assertEqual(service.registry.ids(), default_registry().ids())
        self.assertEqual(service.services.env, {})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
