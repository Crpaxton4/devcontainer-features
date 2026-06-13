import unittest

from odoo_sdk.commands.command_registry import CommandDispatcher

class DummyCommandOk:
    def __init__(self, client):
        self.client = client
    def __call__(self):
        return ("ok", self.client)


class DummyCommandAlt:
    def __init__(self, client):
        self.client = client
    def __call__(self):
        return "alt"


class TestCommandDispatcher(unittest.TestCase):
    def test_execute_registered_command_returns_result_and_receives_client(self):
        client = object()
        dispatcher = CommandDispatcher(client)
        dispatcher.register("dummy", DummyCommandOk)
        result = dispatcher["dummy"]()
        self.assertEqual(result[0], "ok")
        self.assertIs(result[1], client)

    def test_execute_unregistered_command_raises_ValueError(self):
        dispatcher = CommandDispatcher(object())
        with self.assertRaises(KeyError):
            _ = dispatcher["nope"]

    def test_register_overwrites_existing_command(self):
        dispatcher = CommandDispatcher(object())
        dispatcher.register("cmd", DummyCommandAlt)
        res1 = dispatcher["cmd"]()
        self.assertEqual(res1, "alt")

        class NewCmd:
            def __init__(self, client):
                self.client = client
            def __call__(self):
                return "new"

        dispatcher.register("cmd", NewCmd)
        res2 = dispatcher["cmd"]()
        self.assertEqual(res2, "new")


if __name__ == "__main__":
    unittest.main()
