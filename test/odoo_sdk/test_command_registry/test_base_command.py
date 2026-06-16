import unittest

from odoo_sdk.commands.command import Command
from odoo_sdk.commands.command_registry import Registry


class DummyClient:
    def __init__(self, uid: int):
        self.uid = uid


class DummyGetUidCommand(Command):
    _name = "get_uid"
    _description = "Return the current user's UID."

    def execute(self):
        return self._client.uid


class TestDispatcher(unittest.TestCase):
    def test_dispatcher_executes_registered_get_uid_command(self) -> None:
        dispatcher = Registry(DummyClient(uid=42))
        dispatcher.register("get_uid", DummyGetUidCommand)

        self.assertEqual(dispatcher["get_uid"].execute(), 42)

    def test_dispatcher_raises_for_unknown_command(self) -> None:
        dispatcher = Registry(DummyClient(uid=61))
        with self.assertRaises(KeyError):
            _ = dispatcher["missing"]
