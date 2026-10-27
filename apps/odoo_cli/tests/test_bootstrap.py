"""Wiring tests for the single composition root (``odoo_cli.bootstrap``, #716).

These tests pin the composition root itself: the default graph, the injected
overrides and the lazy Odoo client.
"""

import unittest
from datetime import timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, Mock, patch

from odoo_dev_core import CommandService, default_registry
from odoo_dev_core.ports import (
    ChatterGateway,
    Clock,
    GitGateway,
    IssueTracker,
    JobsPort,
    RepoMapPort,
    StateStore,
    TaskRecords,
)
from odoo_dev_data.config import LocalConfig
from odoo_dev_data.repo_map import RepoMap
from odoo_dev_data.tasks import TaskRecord

from odoo_cli.bootstrap import (
    RepoMapStore,
    SystemClock,
    TaskStore,
    bootstrap,
    settings_from_config,
)

from .support import CONFIGURED


class TestBootstrapDefaults(unittest.TestCase):
    def setUp(self):
        self._home = TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        self.env = {"ODOO_DEV_HOME": self._home.name}

    def test_builds_the_service_over_every_builtin_command(self):
        service = bootstrap(env=self.env)
        self.assertIsInstance(service, CommandService)
        self.assertEqual(service.registry.ids(), default_registry().ids())
        self.assertEqual(len(service.registry.ids()), 35)
        self.assertIs(service.services.env, self.env)

    def test_jobs_share_the_event_store_connection(self):
        services = bootstrap(env=self.env).services
        self.assertIsNotNone(services.jobs)
        self.assertIsInstance(services.jobs, JobsPort)
        self.assertIs(services.jobs._conn, services.events._conn)

    def test_config_comes_from_the_written_config_toml(self):
        (Path(self._home.name) / "config.toml").write_text(
            '[connection]\nurl = "https://x.example"\ndb = "d"\n', "utf-8"
        )
        config = bootstrap(env=self.env).services.config
        self.assertEqual(config.connection["url"], "https://x.example")
        self.assertEqual(config.connection["db"], "d")

    def test_lazy_client_is_not_built_until_used(self):
        with patch("odoo_cli.bootstrap.OdooClient") as MockClient:
            service = bootstrap(env=self.env, config=CONFIGURED)
            MockClient.from_settings.assert_not_called()
            client = service.services.client
            MockClient.from_settings.return_value.uid = 7
            self.assertEqual(client.uid, 7)
            client.execute("m", "read", 1)
            _ = client["m"]
        # Built once, from the bootstrap config, and cached for every access.
        MockClient.from_settings.assert_called_once()
        built = MockClient.from_settings.return_value
        built.execute.assert_called_once_with("m", "read", 1)

    def test_lazy_client_refresh_reloads_the_config_file(self):
        reloaded = LocalConfig(
            connection={"url": "https://x", "db": "d", "username": "u", "password": "b"}
        )
        with patch("odoo_cli.bootstrap.OdooClient") as MockClient:
            bootstrap(env=self.env, config=CONFIGURED).services.client.uid
        refresh = MockClient.from_settings.call_args.kwargs["credentials_refresh"]
        with patch("odoo_cli.bootstrap.LocalConfig.load", return_value=reloaded):
            self.assertEqual(refresh().password, "b")

    def test_every_port_is_built(self):
        services = bootstrap(env=self.env, odoo=MagicMock()).services
        self.assertIsInstance(services.events, StateStore)
        self.assertIsInstance(services.git, GitGateway)
        self.assertIsInstance(services.github, IssueTracker)
        self.assertIsInstance(services.chatter, ChatterGateway)
        self.assertIsInstance(services.repo_map, RepoMapPort)
        self.assertIsInstance(services.tasks, TaskRecords)
        self.assertIsInstance(services.clock, Clock)

    def test_events_live_under_the_state_home(self):
        bootstrap(env=self.env, odoo=MagicMock())
        self.assertTrue((Path(self._home.name) / "state.db").exists())


class TestBootstrapOverrides(unittest.TestCase):
    def test_injected_peers_are_used_verbatim(self):
        odoo, events, config = Mock(), Mock(), Mock()
        with patch("odoo_cli.bootstrap.LocalConfig") as MockConfig:
            service = bootstrap(env={}, odoo=odoo, events=events, config=config)

        MockConfig.load.assert_not_called()
        self.assertIs(service.services.client, odoo)
        self.assertIs(service.services.events, events)
        self.assertIs(service.services.config, config)
        # An injected store has no connection to share, so no job ledger.
        self.assertIsNone(service.services.jobs)

    def test_peers_are_keyword_only(self):
        with self.assertRaises(TypeError):
            bootstrap(Mock())  # noqa - the positional form is the contract


class TestBootstrapAdapters(unittest.TestCase):
    def test_system_clock_reads_aware_utc(self):
        self.assertEqual(SystemClock().now().tzinfo, timezone.utc)

    def test_repo_map_store_round_trips_under_its_home(self):
        with TemporaryDirectory() as home:
            store = RepoMapStore(Path(home))
            self.assertEqual(store.load(), RepoMap())
            path = store.save(RepoMap())
            self.assertEqual(path.parent, Path(home))
            self.assertIsNone(store.resolve("nope", cwd=Path(home)).project)

    def test_task_store_round_trips_under_its_home(self):
        with TemporaryDirectory() as home:
            store = TaskStore(Path(home))
            self.assertEqual(store.read(5).sections, {})
            record = TaskRecord(task_id="5", sections={"brief": "b"})
            store.write(record)
            self.assertEqual(store.read(5).sections, {"brief": "b"})
            count = store.append_friction(
                5,
                [{"tool": "t", "expected": "e", "observed": "o", "did": "d"}],
                job_id="job-1",
            )
            self.assertEqual(count, 1)
            self.assertEqual(store.read(5).friction[0]["job_id"], "job-1")


class TestSettingsFromConfig(unittest.TestCase):
    def _config(self, **connection):
        return LocalConfig(connection=connection)

    def test_builds_validated_settings_from_the_config(self):
        settings = settings_from_config(
            self._config(url="https://x", db="d", username="u", password="p")
        )
        self.assertEqual(
            (settings.url, settings.db, settings.username, settings.password),
            ("https://x", "d", "u", "p"),
        )
        self.assertEqual(settings.transport, "xmlrpc")

    def test_explicit_values_win_over_the_config(self):
        settings = settings_from_config(
            self._config(url="https://x", db="d", username="u", password="p"),
            db="other",
        )
        self.assertEqual(settings.db, "other")

    def test_json2_transport_needs_an_api_key(self):
        config = self._config(url="https://x", db="d", transport="json2")
        with self.assertRaises(ValueError):
            settings_from_config(config)
        settings = settings_from_config(config, api_key="k")
        self.assertEqual((settings.transport, settings.api_key), ("json2", "k"))

    def test_missing_settings_raise_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            settings_from_config(self._config())
        self.assertIn("Missing Odoo connection settings", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
