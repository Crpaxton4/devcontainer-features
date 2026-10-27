"""Wiring tests for the single composition root (``odoo_client.bootstrap``, #716).

Additive only: the entrypoint tests (``tests/test_cli/test_cli_main.py``,
``tests/test_mcp/test_main.py``, ``tests/test_tui/test_main.py``) pin the
parse → bootstrap → dispatch → format behavior of the three ``__main__``
modules; these tests pin the composition root itself — the default graph,
injected overrides, and the config-loaded-once contract.
"""

import unittest
from unittest.mock import MagicMock, Mock, patch

from odoo_dev_data.config import LocalConfig

from odoo_client.bootstrap import bootstrap, build_client, settings_from_config
from odoo_client.commands import Registry
from odoo_client.commands.builtin import BUILTIN_COMMANDS


class TestBootstrapDefaults(unittest.TestCase):
    def test_default_graph_builds_client_and_loads_config_once(self):
        with (
            patch("odoo_client.bootstrap.OdooClient") as MockClient,
            patch("odoo_client.bootstrap.LocalConfig") as MockConfig,
            patch("odoo_client.bootstrap.settings_from_config") as settings,
        ):
            registry = bootstrap()

        MockClient.from_settings.assert_called_once()
        self.assertEqual(
            MockClient.from_settings.call_args.args, (settings.return_value,)
        )
        MockConfig.load.assert_called_once_with()
        self.assertIsInstance(registry, Registry)
        # The one loaded config and the one built client are injected into
        # every command the registry resolves.
        command = registry["query_sessions"]
        self.assertIs(command._client, MockClient.from_settings.return_value)
        self.assertIs(command.config, MockConfig.load.return_value)

    def test_registers_all_builtins(self):
        with (
            patch("odoo_client.bootstrap.OdooClient"),
            patch("odoo_client.bootstrap.LocalConfig"),
            patch("odoo_client.bootstrap.settings_from_config"),
        ):
            registry = bootstrap()

        registered = [name for name, _ in registry.items()]
        self.assertEqual(set(registered), set(BUILTIN_COMMANDS))

    def test_state_stays_lazy_when_omitted(self):
        # Merely assembling a graph must never force the SQLite tracker DB
        # into existence (the MCP entry point relies on this).
        with (
            patch("odoo_client.bootstrap.OdooClient"),
            patch("odoo_client.bootstrap.LocalConfig"),
            patch("odoo_client.bootstrap.settings_from_config"),
            patch("odoo_client.commands.command_registry.EventStore") as MockState,
        ):
            bootstrap()

        MockState.assert_not_called()


class TestBootstrapOverrides(unittest.TestCase):
    def test_injected_peers_are_used_verbatim(self):
        client, state, config = Mock(), Mock(), Mock()
        with (
            patch("odoo_client.bootstrap.OdooClient") as MockClient,
            patch("odoo_client.bootstrap.LocalConfig") as MockConfig,
        ):
            registry = bootstrap(client=client, state=state, config=config)

        # Explicit peers suppress every default construction.
        MockClient.from_settings.assert_not_called()
        MockConfig.load.assert_not_called()
        command = registry["query_sessions"]
        self.assertIs(command._client, client)
        self.assertIs(command.state, state)
        self.assertIs(command.config, config)
        self.assertIs(registry.state_client, state)

    def test_partial_override_still_loads_config_once(self):
        client = MagicMock()
        with (
            patch("odoo_client.bootstrap.OdooClient") as MockClient,
            patch("odoo_client.bootstrap.LocalConfig") as MockConfig,
        ):
            registry = bootstrap(client=client)

        MockClient.from_settings.assert_not_called()
        MockConfig.load.assert_called_once_with()
        self.assertIs(registry["query_sessions"].config, MockConfig.load.return_value)

    def test_peers_are_keyword_only(self):
        with self.assertRaises(TypeError):
            bootstrap(Mock())  # noqa — the positional form is the contract


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
        with patch("odoo_client.bootstrap.OdooClient") as MockClient:
            build_client(config)
        refresh = MockClient.from_settings.call_args.kwargs["credentials_refresh"]
        with patch("odoo_client.bootstrap.LocalConfig.load", return_value=reloaded):
            self.assertEqual(refresh().password, "b")


if __name__ == "__main__":
    unittest.main()
