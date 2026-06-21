import unittest
from unittest.mock import MagicMock

from odoo_sdk.commands import Registry
from odoo_sdk.commands.builtin import (
    BUILTIN_COMMANDS,
    CreateTaskCommand,
    GetModelsCommand,
    GetTasksCommand,
    GetTodoCommand,
    GetUidCommand,
    register_builtins,
)

TASK_FIELDS = ["name", "project_id", "stage_id", "user_ids", "date_deadline"]


class TestGetUidCommand(unittest.TestCase):
    def test_returns_client_uid(self):
        client = MagicMock()
        client.uid = 42
        self.assertEqual(GetUidCommand(client).execute(), 42)


class TestGetModelsCommand(unittest.TestCase):
    def test_reads_model_names(self):
        client = MagicMock()
        models = client.__getitem__.return_value
        models.search.return_value.read.return_value = [{"model": "res.partner"}]

        result = GetModelsCommand(client).execute()

        client.__getitem__.assert_called_once_with("ir.model")
        models.search.assert_called_once_with([])
        models.search.return_value.read.assert_called_once_with(["model", "name"])
        self.assertEqual(result, [{"model": "res.partner"}])


class TestGetTasksCommand(unittest.TestCase):
    def test_defaults_to_empty_domain_and_limit_ten(self):
        client = MagicMock()
        tasks = client.__getitem__.return_value
        tasks.search.return_value.read.return_value = [{"name": "T"}]

        result = GetTasksCommand(client).execute()

        client.__getitem__.assert_called_once_with("project.task")
        tasks.search.assert_called_once_with([], limit=10)
        tasks.search.return_value.read.assert_called_once_with(TASK_FIELDS)
        self.assertEqual(result, [{"name": "T"}])

    def test_passes_through_domain_and_limit(self):
        client = MagicMock()
        tasks = client.__getitem__.return_value
        domain = [("stage_id", "=", 3)]

        GetTasksCommand(client).execute(domain=domain, limit=5)

        tasks.search.assert_called_once_with(domain, limit=5)


class TestGetTodoCommand(unittest.TestCase):
    def test_returns_first_record_when_found(self):
        client = MagicMock()
        tasks = client.__getitem__.return_value
        tasks.search.return_value.read.return_value = [{"name": "T"}, {"name": "U"}]

        result = GetTodoCommand(client).execute(7)

        client.__getitem__.assert_called_once_with("project.task")
        tasks.search.assert_called_once_with([("id", "=", 7)], limit=1)
        self.assertEqual(result, {"name": "T"})

    def test_returns_none_when_missing(self):
        client = MagicMock()
        tasks = client.__getitem__.return_value
        tasks.search.return_value.read.return_value = []

        self.assertIsNone(GetTodoCommand(client).execute(99))


class TestCreateTaskCommand(unittest.TestCase):
    def test_creates_task_with_prefixed_name(self):
        client = MagicMock()
        tasks = client.__getitem__.return_value
        tasks.create.return_value = 1001

        result = CreateTaskCommand(client).execute("Do it", 4, "details")

        client.__getitem__.assert_called_once_with("project.task")
        tasks.create.assert_called_once_with(
            {"name": "[MCP] Do it", "project_id": 4, "description": "details"}
        )
        self.assertEqual(result, 1001)

    def test_description_defaults_to_empty(self):
        client = MagicMock()
        tasks = client.__getitem__.return_value

        CreateTaskCommand(client).execute("Quick", 2)

        tasks.create.assert_called_once_with(
            {"name": "[MCP] Quick", "project_id": 2, "description": ""}
        )


class TestRegisterBuiltins(unittest.TestCase):
    def test_registers_all_builtins(self):
        registry = register_builtins(Registry(MagicMock()))
        registered = {name for name, _ in registry.items()}
        self.assertEqual(registered, set(BUILTIN_COMMANDS))

    def test_returns_same_registry(self):
        registry = Registry(MagicMock())
        self.assertIs(register_builtins(registry), registry)

    def test_metadata_matches_registration_keys(self):
        for name, command in BUILTIN_COMMANDS.items():
            self.assertEqual(command(MagicMock()).name, name)


if __name__ == "__main__":
    unittest.main()
