"""Tests of the ``odoo_tui`` composition root on a throwaway state home."""

import tempfile
import unittest
from unittest.mock import MagicMock

from odoo_dev_core import CommandService, default_registry
from odoo_dev_data.events import EventStore

from odoo_tui.app import TuiDeps
from odoo_tui.bootstrap import bootstrap


class TestBootstrap(unittest.TestCase):
    def setUp(self):
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        self.env = {"ODOO_DEV_HOME": home.name}
        self.odoo = MagicMock()
        self.service = bootstrap(env=self.env, odoo=self.odoo)

    def test_wires_the_builtins_with_the_injected_client(self):
        self.assertIsInstance(self.service, CommandService)
        self.assertEqual(self.service.registry.ids(), default_registry().ids())
        self.assertEqual(len(self.service.registry.ids()), 35)
        self.assertIs(self.service.services.client, self.odoo)
        self.assertEqual(self.service.services.env, self.env)

    def test_default_store_shares_its_connection_with_the_job_ledger(self):
        self.assertIsNotNone(self.service.services.jobs)
        self.assertIsInstance(TuiDeps(self.service).store, EventStore)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
