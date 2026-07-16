import unittest

from odoo_sdk.commands.command import Command
from odoo_sdk.commands.command_registry import Registry


class DummyCommandOk(Command):
    _name = "dummy_ok"
    _description = "dummy ok command"

    def execute(self):
        return ("ok", self._client)


class DummyCommandAlt(Command):
    _name = "dummy_alt"
    _description = "dummy alt command"

    def execute(self):
        return "alt"


class TestRegistry(unittest.TestCase):
    def test_execute_registered_command_returns_result_and_receives_client(self):
        client = object()
        dispatcher = Registry(client)
        dispatcher.register("dummy", DummyCommandOk)
        result = dispatcher["dummy"].execute()
        self.assertEqual(result[0], "ok")
        self.assertIs(result[1], client)

    def test_execute_unregistered_command_raises_ValueError(self):
        dispatcher = Registry(object())
        with self.assertRaises(KeyError):
            _ = dispatcher["nope"]

    def test_register_overwrites_existing_command(self):
        dispatcher = Registry(object())
        dispatcher.register("cmd", DummyCommandAlt)
        res1 = dispatcher["cmd"].execute()
        self.assertEqual(res1, "alt")

        class NewCmd(Command):
            _name = "new"
            _description = "new command"

            def execute(self):
                return "new"

        dispatcher.register("cmd", NewCmd)
        res2 = dispatcher["cmd"].execute()
        self.assertEqual(res2, "new")


class TestRegistryItems(unittest.TestCase):
    def test_items_yields_name_and_client_bound_instance(self):
        client = object()
        registry = Registry(client)
        registry.register("dummy", DummyCommandOk)

        items = dict(registry.items())

        self.assertEqual(set(items), {"dummy"})
        self.assertIsInstance(items["dummy"], DummyCommandOk)
        self.assertIs(items["dummy"]._client, client)

    def test_items_empty_registry_is_empty(self):
        self.assertEqual(list(Registry(object()).items()), [])

    def test_iter_yields_registered_classes(self):
        registry = Registry(object())
        registry.register("ok", DummyCommandOk)
        registry.register("alt", DummyCommandAlt)

        self.assertEqual(set(registry), {DummyCommandOk, DummyCommandAlt})

    def test_items_covers_all_registered_commands(self):
        registry = Registry(object())
        registry.register("ok", DummyCommandOk)
        registry.register("alt", DummyCommandAlt)

        self.assertEqual({name for name, _ in registry.items()}, {"ok", "alt"})


if __name__ == "__main__":
    unittest.main()


class TestRegistryDependencyInjection(unittest.TestCase):
    def test_injects_state_and_config_into_command_instances(self):
        from unittest.mock import Mock

        from odoo_sdk.commands.command import Command

        class TrackingCommand(Command):
            _name = "tracked"
            _description = "tracked"

            def execute(self):
                return None

        client = Mock()
        state = Mock()
        config = Mock()
        registry = Registry(client, state_client=state, config=config)
        registry.register("tracked", TrackingCommand)

        cmd = registry["tracked"]
        self.assertIs(cmd._client, client)
        self.assertIs(cmd.state, state)
        self.assertIs(cmd.config, config)

    def test_positional_client_only_still_supported(self):
        client = object()
        registry = Registry(client)
        registry.register("dummy", DummyCommandOk)
        result = registry["dummy"].execute()
        self.assertIs(result[1], client)
