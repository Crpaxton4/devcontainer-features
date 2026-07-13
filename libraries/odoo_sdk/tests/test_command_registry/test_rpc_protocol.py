"""Prove the command layer runs on any structural ``RpcClient``.

These tests deliberately never import :class:`OdooClient`. A fake object that
merely exposes the three members of
:class:`~odoo_sdk.commands.protocols.RpcClient` (``uid``, ``execute`` and
``__getitem__``) drives real command logic end-to-end through the
:class:`~odoo_sdk.commands.command_registry.Registry`.
"""

import unittest

from odoo_sdk.commands import Command, Registry


class FakeRecordset:
    """Minimal stand-in for the recordset returned by ``client[model]``."""

    def __init__(self, rows):
        self._rows = rows
        self.searched_domain = None

    def search(self, domain):
        self.searched_domain = domain
        return self

    def read(self, fields):
        return [{key: row[key] for key in fields} for row in self._rows]


class FakeRpcClient:
    """A structural ``RpcClient`` with no dependency on ``OdooClient``."""

    def __init__(self, uid, models=None):
        self._uid = uid
        self._recordsets = {
            name: FakeRecordset(rows) for name, rows in (models or {}).items()
        }
        self.execute_calls = []

    @property
    def uid(self):
        return self._uid

    def execute(self, model, method, *args, **kwargs):
        self.execute_calls.append((model, method, args, kwargs))
        return {"model": model, "method": method}

    def __getitem__(self, model_name):
        return self._recordsets[model_name]


class UidCommand(Command):
    """Command whose only dependency is ``client.uid``."""

    _name = "uid"
    _description = "Return the authenticated user id."

    def execute(self):
        return self._client.uid


class ModelReadCommand(Command):
    """Command that reaches the transport via ``client[...]``."""

    _name = "models"
    _description = "Read model names."

    def execute(self):
        return self._client["ir.model"].search([]).read(["model", "name"])


class ExecuteEchoCommand(Command):
    """Command that reaches the transport via ``client.execute``."""

    _name = "echo"
    _description = "Echo an execute() call."

    def execute(self, model, method):
        return self._client.execute(model, method)


class TestFakeRpcClientDrivesCommands(unittest.TestCase):
    def test_uid_member_drives_command_end_to_end(self):
        registry = Registry(FakeRpcClient(uid=7))
        registry.register("uid", UidCommand)

        self.assertEqual(registry["uid"].execute(), 7)

    def test_getitem_member_drives_command_end_to_end(self):
        rows = [{"model": "res.partner", "name": "Contact", "extra": "drop"}]
        registry = Registry(FakeRpcClient(uid=1, models={"ir.model": rows}))
        registry.register("models", ModelReadCommand)

        result = registry["models"].execute()

        self.assertEqual(result, [{"model": "res.partner", "name": "Contact"}])

    def test_execute_member_drives_command_end_to_end(self):
        client = FakeRpcClient(uid=1)
        registry = Registry(client)
        registry.register("echo", ExecuteEchoCommand)

        result = registry["echo"].execute("res.users", "read")

        self.assertEqual(result, {"model": "res.users", "method": "read"})
        self.assertEqual(client.execute_calls, [("res.users", "read", (), {})])


if __name__ == "__main__":
    unittest.main()
