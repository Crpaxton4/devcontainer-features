"""Wiring tests for the single composition root (``odoo_cli.bootstrap``, #716).

The entrypoint tests (``tests/test_cli/test_cli_main.py``,
``tests/test_mcp/test_main.py``, ``tests/test_tui/test_main.py``) pin the
parse, bootstrap, dispatch, format behavior of the three ``__main__``
modules; these tests pin the composition root itself - the default graph,
injected overrides, and the config-loaded-once contract.
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
    build_client,
    settings_from_config,
)


class TestBootstrapDefaults(unittest.TestCase):
    def setUp(self):
        self._home = TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        self.env = {"ODOO_DEV_HOME": self._home.name}

    def test_default_graph_builds_client_and_loads_config_once(self):
        with (
            patch("odoo_cli.bootstrap.OdooClient") as MockClient,
            patch("odoo_cli.bootstrap.LocalConfig") as MockConfig,
            patch("odoo_cli.bootstrap.settings_from_config") as settings,
        ):
            service = bootstrap(env=self.env)

        MockClient.from_settings.assert_called_once()
        self.assertEqual(
            MockClient.from_settings.call_args.args, (settings.return_value,)
        )
        MockConfig.load.assert_called_once_with(env=self.env)
        self.assertIsInstance(service, CommandService)
        # The one loaded config and the one built client are shared by every
        # command through the Services bundle.
        self.assertIs(service.services.client, MockClient.from_settings.return_value)
        self.assertIs(service.services.config, MockConfig.load.return_value)
        self.assertIs(service.services.env, self.env)

    def test_registers_all_builtins(self):
        service = bootstrap(env=self.env, client=MagicMock(), config=LocalConfig())
        self.assertEqual(service.registry.ids(), default_registry().ids())

    def test_every_port_is_built(self):
        services = bootstrap(
            env=self.env, client=MagicMock(), config=LocalConfig()
        ).services
        self.assertIsInstance(services.events, StateStore)
        self.assertIsInstance(services.git, GitGateway)
        self.assertIsInstance(services.github, IssueTracker)
        self.assertIsInstance(services.chatter, ChatterGateway)
        self.assertIsInstance(services.repo_map, RepoMapPort)
        self.assertIsInstance(services.tasks, TaskRecords)
        self.assertIsInstance(services.clock, Clock)

    def test_events_live_under_the_state_home(self):
        bootstrap(env=self.env, client=MagicMock(), config=LocalConfig())
        self.assertTrue((Path(self._home.name) / "state.db").exists())

    def test_job_ledger_is_built_only_inside_a_dispatched_job(self):
        plain = bootstrap(env=self.env, client=MagicMock(), config=LocalConfig())
        self.assertIsNone(plain.services.jobs)
        job_env = {**self.env, "ODOO_JOB_ID": "job-1"}
        dispatched = bootstrap(env=job_env, client=MagicMock(), config=LocalConfig())
        self.assertIsInstance(dispatched.services.jobs, JobsPort)


class TestBootstrapOverrides(unittest.TestCase):
    def test_injected_peers_are_used_verbatim(self):
        client, events, config = Mock(), Mock(), Mock()
        with (
            patch("odoo_cli.bootstrap.OdooClient") as MockClient,
            patch("odoo_cli.bootstrap.LocalConfig") as MockConfig,
        ):
            service = bootstrap(env={}, client=client, events=events, config=config)

        # Explicit peers suppress every default construction.
        MockClient.from_settings.assert_not_called()
        MockConfig.load.assert_not_called()
        self.assertIs(service.services.client, client)
        self.assertIs(service.services.events, events)
        self.assertIs(service.services.config, config)

    def test_partial_override_still_loads_config_once(self):
        client = MagicMock()
        with TemporaryDirectory() as home:
            with (
                patch("odoo_cli.bootstrap.OdooClient") as MockClient,
                patch("odoo_cli.bootstrap.LocalConfig") as MockConfig,
            ):
                service = bootstrap(env={"ODOO_DEV_HOME": home}, client=client)

        MockClient.from_settings.assert_not_called()
        MockConfig.load.assert_called_once()
        self.assertIs(service.services.config, MockConfig.load.return_value)

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


class TestBuildClient(unittest.TestCase):
    def test_refresh_reloads_the_config_file(self):
        config = LocalConfig(
            connection={"url": "https://x", "db": "d", "username": "u", "password": "a"}
        )
        reloaded = LocalConfig(
            connection={"url": "https://x", "db": "d", "username": "u", "password": "b"}
        )
        with patch("odoo_cli.bootstrap.OdooClient") as MockClient:
            build_client(config)
        refresh = MockClient.from_settings.call_args.kwargs["credentials_refresh"]
        with patch("odoo_cli.bootstrap.LocalConfig.load", return_value=reloaded):
            self.assertEqual(refresh().password, "b")


if __name__ == "__main__":
    unittest.main()
