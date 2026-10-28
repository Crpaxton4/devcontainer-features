"""Tests of the ``odoo_mcp`` composition root on a temporary ``ODOO_DEV_HOME``."""

import os
import unittest
from unittest.mock import MagicMock, patch

from odoo_dev_core import CommandService, default_registry
from odoo_dev_data.config import LocalConfig
from odoo_dev_data.events import EventStore
from odoo_dev_data.jobs import JobStore

from odoo_mcp.bootstrap import assert_configured, bootstrap


class TestBootstrap(unittest.TestCase):
    def _service(self, **kwargs):
        env = {"ODOO_DEV_HOME": os.environ["ODOO_DEV_HOME"]}
        return bootstrap(env=env, odoo=kwargs.pop("odoo", MagicMock()), **kwargs)

    def test_wires_the_builtins_around_the_injected_client(self):
        odoo = MagicMock()
        service = self._service(odoo=odoo)
        self.assertIsInstance(service, CommandService)
        self.assertEqual(service.registry.ids(), default_registry().ids())
        self.assertIs(service.services.client, odoo)

    def test_event_store_and_job_ledger_share_one_connection(self):
        services = self._service().services
        self.assertIsInstance(services.events, EventStore)
        self.assertIsInstance(services.jobs, JobStore)
        self.assertTrue(
            os.path.exists(os.path.join(os.environ["ODOO_DEV_HOME"], "state.db"))
        )

    def test_injected_events_leave_no_job_ledger(self):
        events = MagicMock()
        services = self._service(events=events).services
        self.assertIs(services.events, events)
        self.assertIsNone(services.jobs)

    def test_builds_the_client_eagerly_when_none_is_injected(self):
        config = LocalConfig(
            connection={"url": "http://x", "db": "d", "username": "u", "password": "p"}
        )
        with patch("odoo_mcp.bootstrap.OdooClient") as client:
            env = {"ODOO_DEV_HOME": os.environ["ODOO_DEV_HOME"]}
            service = bootstrap(env=env, config=config)
        client.from_settings.assert_called_once()
        self.assertIs(service.services.client, client.from_settings.return_value)

    def test_stores_and_clock_are_bound_to_the_home(self):
        services = self._service().services
        self.assertEqual(services.tasks.read(5).task_id, "5")
        self.assertEqual(services.clock.now().tzinfo.utcoffset(None).total_seconds(), 0)
        self.assertEqual(services.env["ODOO_DEV_HOME"], os.environ["ODOO_DEV_HOME"])

    def test_assert_configured_raises_without_connection_settings(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(ValueError):
                assert_configured(LocalConfig(connection={}))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
