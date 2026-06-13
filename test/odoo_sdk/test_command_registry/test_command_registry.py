import unittest

from odoo_sdk.commands.command_registry import Registry


class DummyCommandOk:
    def __init__(self, client):
        self.client = client

    def execute(self):
        return ("ok", self.client)


class DummyCommandAlt:
    def __init__(self, client):
        self.client = client

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

        class NewCmd:
            def __init__(self, client):
                self.client = client

            def execute(self):
                return "new"

        dispatcher.register("cmd", NewCmd)
        res2 = dispatcher["cmd"].execute()
        self.assertEqual(res2, "new")


if __name__ == "__main__":
    unittest.main()
