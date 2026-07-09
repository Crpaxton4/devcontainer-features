import unittest
from unittest.mock import Mock, patch

from odoo_sdk.commands.command import Command


class ConcreteCommand(Command):
    """Minimal concrete subclass for testing Command base behaviour."""

    _name = "concrete"
    _description = "A concrete command"

    def execute(self, *args, **kwargs):
        return None


class TestCommandProtocolDefaultBodies(unittest.TestCase):
    def test_init_with_client_stores_provided_client(self):
        mock_client = Mock()
        cmd = ConcreteCommand(client=mock_client)
        self.assertIs(cmd._client, mock_client)

    def test_init_without_client_creates_odoo_client(self):
        mock_client_instance = Mock()
        with patch(
            "odoo_sdk.commands.command.OdooClient",
            return_value=mock_client_instance,
        ) as MockClient:
            cmd = ConcreteCommand()
            MockClient.assert_called_once()
            self.assertIs(cmd._client, mock_client_instance)

    def test_execute_returns_none_by_default(self):
        mock_client = Mock()
        cmd = ConcreteCommand(client=mock_client)
        result = cmd.execute()
        self.assertIsNone(result)

    def test_name_property_returns_class_name(self):
        cmd = ConcreteCommand(client=Mock())
        self.assertEqual(cmd.name, "concrete")

    def test_description_property_returns_class_description(self):
        cmd = ConcreteCommand(client=Mock())
        self.assertEqual(cmd.description, "A concrete command")


class TestCommandDependencyInjection(unittest.TestCase):
    def test_injected_state_is_returned(self):
        state = Mock()
        cmd = ConcreteCommand(client=Mock(), state=state)
        self.assertIs(cmd.state, state)

    def test_injected_config_is_returned(self):
        config = Mock()
        cmd = ConcreteCommand(client=Mock(), config=config)
        self.assertIs(cmd.config, config)

    def test_state_lazily_created_when_absent(self):
        created = Mock()
        with patch(
            "odoo_sdk.commands.command.LocalStateClient", return_value=created
        ) as MockState:
            cmd = ConcreteCommand(client=Mock())
            got = cmd.state
        MockState.assert_called_once_with()
        self.assertIs(got, created)
        # Second access reuses the cached instance.
        self.assertIs(cmd.state, created)

    def test_config_lazily_loaded_when_absent(self):
        loaded = Mock()
        with patch(
            "odoo_sdk.commands.command.LocalConfig.load", return_value=loaded
        ) as mock_load:
            cmd = ConcreteCommand(client=Mock())
            got = cmd.config
        mock_load.assert_called_once_with()
        self.assertIs(got, loaded)
        self.assertIs(cmd.config, loaded)
