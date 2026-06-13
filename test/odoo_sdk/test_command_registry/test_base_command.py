import unittest

from odoo_sdk.commands.command_registry import CommandDispatcher

class DummyClient:
    def __init__(self, uid: int):
        self.uid = uid


class DummyGetUidCommand:
    def __init__(self, client):
        self.client = client

    def __call__(self):
        return self.client.uid


class TestDispatcher(unittest.TestCase):
    def test_dispatcher_executes_registered_get_uid_command(self) -> None:
        dispatcher = CommandDispatcher(DummyClient(uid=42))
        dispatcher.register("get_uid", DummyGetUidCommand)

        self.assertEqual(dispatcher["get_uid"](), 42)

    def test_dispatcher_raises_for_unknown_command(self) -> None:
        dispatcher = CommandDispatcher(DummyClient(uid=61))
        with self.assertRaises(KeyError):
            _ = dispatcher["missing"]
