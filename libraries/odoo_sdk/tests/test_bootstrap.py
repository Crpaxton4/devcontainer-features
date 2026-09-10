"""Wiring tests for the single composition root (``odoo_sdk.bootstrap``, #716).

Additive only: the entrypoint tests (``tests/test_cli/test_cli_main.py``,
``tests/test_mcp/test_main.py``, ``tests/test_tui/test_main.py``) pin the
parse → bootstrap → dispatch → format behavior of the three ``__main__``
modules; these tests pin the composition root itself — the default graph,
injected overrides, and the config-loaded-once contract.
"""

import unittest
from unittest.mock import MagicMock, Mock, patch

from odoo_sdk.bootstrap import bootstrap
from odoo_sdk.commands import Registry
from odoo_sdk.commands.builtin import BUILTIN_COMMANDS


class TestBootstrapDefaults(unittest.TestCase):
    def test_default_graph_builds_client_and_loads_config_once(self):
        with (
            patch("odoo_sdk.bootstrap.OdooClient") as MockClient,
            patch("odoo_sdk.bootstrap.LocalConfig") as MockConfig,
        ):
            registry = bootstrap()

        MockClient.assert_called_once_with()
        MockConfig.load.assert_called_once_with()
        self.assertIsInstance(registry, Registry)
        # The one loaded config and the one built client are injected into
        # every command the registry resolves.
        command = registry["list_runs"]
        self.assertIs(command._client, MockClient.return_value)
        self.assertIs(command.config, MockConfig.load.return_value)

    def test_registers_all_builtins(self):
        with (
            patch("odoo_sdk.bootstrap.OdooClient"),
            patch("odoo_sdk.bootstrap.LocalConfig"),
        ):
            registry = bootstrap()

        registered = [name for name, _ in registry.items()]
        self.assertEqual(set(registered), set(BUILTIN_COMMANDS))

    def test_state_stays_lazy_when_omitted(self):
        # Merely assembling a graph must never force the SQLite tracker DB
        # into existence (the MCP entry point relies on this).
        with (
            patch("odoo_sdk.bootstrap.OdooClient"),
            patch("odoo_sdk.bootstrap.LocalConfig"),
            patch("odoo_sdk.commands.command_registry.LocalStateClient") as MockState,
        ):
            bootstrap()

        MockState.assert_not_called()


class TestBootstrapOverrides(unittest.TestCase):
    def test_injected_peers_are_used_verbatim(self):
        client, state, config = Mock(), Mock(), Mock()
        with (
            patch("odoo_sdk.bootstrap.OdooClient") as MockClient,
            patch("odoo_sdk.bootstrap.LocalConfig") as MockConfig,
        ):
            registry = bootstrap(client=client, state=state, config=config)

        # Explicit peers suppress every default construction.
        MockClient.assert_not_called()
        MockConfig.load.assert_not_called()
        command = registry["list_runs"]
        self.assertIs(command._client, client)
        self.assertIs(command.state, state)
        self.assertIs(command.config, config)
        self.assertIs(registry.state_client, state)

    def test_partial_override_still_loads_config_once(self):
        client = MagicMock()
        with (
            patch("odoo_sdk.bootstrap.OdooClient") as MockClient,
            patch("odoo_sdk.bootstrap.LocalConfig") as MockConfig,
        ):
            registry = bootstrap(client=client)

        MockClient.assert_not_called()
        MockConfig.load.assert_called_once_with()
        self.assertIs(registry["list_runs"].config, MockConfig.load.return_value)

    def test_peers_are_keyword_only(self):
        with self.assertRaises(TypeError):
            bootstrap(Mock())  # noqa — the positional form is the contract


if __name__ == "__main__":
    unittest.main()
