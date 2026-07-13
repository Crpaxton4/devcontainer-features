import unittest
from unittest.mock import MagicMock, patch

import odoo_sdk.commands.builtin as builtin_pkg
from odoo_sdk.commands import Command, Registry
from odoo_sdk.commands.builtin import (
    BUILTIN_COMMANDS,
    CreateTaskCommand,
    GetModelsCommand,
    GetTaskCommand,
    GetTaskChatterCommand,
    GetTasksCommand,
    GetTodoCommand,
    GetUidCommand,
    builtin_command,
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


class TestGetTaskAndChatterInBuiltins(unittest.TestCase):
    def test_get_task_in_builtin_commands(self):
        self.assertIn("get_task", BUILTIN_COMMANDS)
        self.assertIs(BUILTIN_COMMANDS["get_task"], GetTaskCommand)

    def test_get_task_chatter_in_builtin_commands(self):
        self.assertIn("get_task_chatter", BUILTIN_COMMANDS)
        self.assertIs(BUILTIN_COMMANDS["get_task_chatter"], GetTaskChatterCommand)


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


#: The exact built-in command surface, pinned independently of the
#: decorator-populated ``BUILTIN_COMMANDS`` so a dropped ``@builtin_command`` (or
#: an unimported command module) fails loudly instead of silently shrinking the
#: registry past the self-referential parity tests.
EXPECTED_BUILTIN_NAMES = frozenset(
    {
        "get_uid",
        "get_models",
        "get_tasks",
        "get_todo",
        "get_task",
        "get_task_chatter",
        "get_task_attachments",
        "create_task",
        "search_chatter",
        "search_projects",
        "search_tasks",
        "start_task",
        "stop_task",
        "abort_task",
        "resume_task",
        "task_status",
        "task_note",
        "task_list",
        "task_aging",
        "task_question",
        "optimize_sessions",
        "ingest_sessions",
        "query_sessions",
        "timesheet_summary",
    }
)


class TestBuiltinCommandsExactSet(unittest.TestCase):
    """``BUILTIN_COMMANDS`` matches an explicit, hand-pinned name set."""

    def test_exact_command_name_set(self):
        self.assertEqual(set(BUILTIN_COMMANDS), set(EXPECTED_BUILTIN_NAMES))

    def test_no_duplicate_or_missing_count(self):
        self.assertEqual(len(BUILTIN_COMMANDS), len(EXPECTED_BUILTIN_NAMES))


class TestBuiltinCommandDecorator(unittest.TestCase):
    """The ``@builtin_command`` decorator populates ``BUILTIN_COMMANDS``."""

    def test_registers_class_under_its_name(self):
        class _Fresh(Command):
            _name = "fresh_probe_cmd"
            _description = "probe"

            def execute(self):  # pragma: no cover - never invoked
                return None

        # patch.dict leaves BUILTIN_COMMANDS untouched after the block, so this
        # probe registration does not leak into the real built-in surface.
        with patch.dict(BUILTIN_COMMANDS, clear=False):
            returned = builtin_command(_Fresh)
            # The decorator is transparent (returns the class) and keys by ``_name``.
            self.assertIs(returned, _Fresh)
            self.assertIs(BUILTIN_COMMANDS["fresh_probe_cmd"], _Fresh)
        self.assertNotIn("fresh_probe_cmd", BUILTIN_COMMANDS)

    def test_duplicate_name_raises(self):
        class _Dupe(Command):
            _name = "get_uid"  # collides with the real GetUidCommand
            _description = "dupe"

            def execute(self):  # pragma: no cover - never invoked
                return None

        with self.assertRaises(ValueError) as ctx:
            builtin_command(_Dupe)
        self.assertIn("get_uid", str(ctx.exception))
        # The collision left the genuine command in place (no silent overwrite).
        self.assertIs(BUILTIN_COMMANDS["get_uid"], GetUidCommand)

    def test_keys_equal_each_class_name_attribute(self):
        # Determinism: every registration key is exactly the class's ``_name``.
        for name, command in BUILTIN_COMMANDS.items():
            self.assertEqual(command._name, name)


class TestBuiltinExports(unittest.TestCase):
    """``__all__`` is derived from the decorator-populated registry."""

    def test_all_entries_are_real_attributes(self):
        for name in builtin_pkg.__all__:
            self.assertTrue(
                hasattr(builtin_pkg, name), f"{name} in __all__ is not exported"
            )

    def test_all_covers_every_registered_command_class(self):
        exported = set(builtin_pkg.__all__)
        for command in BUILTIN_COMMANDS.values():
            self.assertIn(command.__name__, exported)

    def test_all_includes_registration_api(self):
        for name in ("BUILTIN_COMMANDS", "builtin_command", "register_builtins"):
            self.assertIn(name, builtin_pkg.__all__)


if __name__ == "__main__":
    unittest.main()
