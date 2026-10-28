import unittest
from unittest.mock import MagicMock, patch

from odoo_dev_data.config import ModelIdsNotWritableError
from ...fakes import service
from odoo_dev_core import default_registry

TASK_FIELDS = ["name", "project_id", "stage_id", "user_ids", "date_deadline"]


class TestGetUidCommand(unittest.TestCase):
    def test_returns_client_uid(self):
        client = MagicMock()
        client.uid = 42
        self.assertEqual(service(client=client).execute("get_uid"), 42)


class TestGetEmployeeIdCommand(unittest.TestCase):
    def test_delegates_to_the_billing_resolver(self):
        client = MagicMock()
        state = MagicMock()
        with patch(
            "odoo_dev_core.builtins.get_employee_id.resolve_employee_id",
            return_value=49,
        ) as resolver:
            result = service(client=client, events=state).execute("get_employee_id")

        # Delegation, not a reimplementation: the billing writer and the export
        # path must never disagree about who the authenticated user is (#499).
        resolver.assert_called_once_with(client, state)
        self.assertEqual(result, 49)


class TestGetModelsCommand(unittest.TestCase):
    """The ir.model read, and the opt-in [model_ids] write behind it (#890)."""

    def _client(self, rows=None):
        client = MagicMock()
        models = client.__getitem__.return_value
        models.search.return_value.read.return_value = rows or [
            {"id": 71, "model": "project.task", "name": "Task"},
            {"id": 12, "model": "res.partner", "name": "Contact"},
        ]
        return client, models

    def test_reads_model_names(self):
        client, models = self._client(rows=[{"id": 12, "model": "res.partner"}])

        result = service(client=client).execute("get_models")

        client.__getitem__.assert_called_once_with("ir.model")
        models.search.assert_called_once_with([])
        models.search.return_value.read.assert_called_once_with(["model", "name"])
        self.assertEqual(result["models"], [{"id": 12, "model": "res.partner"}])

    def test_writes_nothing_when_persist_is_omitted(self):
        # Reading ir.model is gated but harmless; writing the config file is
        # opt-in, so the default call stays a pure read.
        client, _ = self._client()
        config = MagicMock()

        result = service(client=client, config=config).execute("get_models")

        config.set_model_id.assert_not_called()
        self.assertEqual(result["persisted"], {})
        self.assertNotIn("warning", result)

    def test_persist_writes_the_named_id_through_the_config(self):
        client, _ = self._client()
        config = MagicMock()

        result = service(client=client, config=config).execute(
            "get_models", persist=["project.task"]
        )

        config.set_model_id.assert_called_once_with("project.task", 71)
        self.assertEqual(result["persisted"], {"project.task": 71})
        self.assertNotIn("warning", result)

    def test_an_unwritable_config_warns_instead_of_failing_the_read(self):
        client, _ = self._client()
        config = MagicMock()
        config.set_model_id.side_effect = ModelIdsNotWritableError("read-only")

        result = service(client=client, config=config).execute(
            "get_models", persist=["project.task"]
        )

        # The read the caller asked for must never be lost to a filesystem
        # permission — the hand-edit path still works, and the warning says so.
        self.assertEqual(len(result["models"]), 2)
        self.assertEqual(result["persisted"], {})
        self.assertIn("read-only", result["warning"])

    def test_a_model_absent_from_ir_model_warns_by_name(self):
        client, _ = self._client()
        config = MagicMock()

        result = service(client=client, config=config).execute(
            "get_models", persist=["not.a.model"]
        )

        config.set_model_id.assert_not_called()
        self.assertEqual(result["persisted"], {})
        self.assertIn("not.a.model", result["warning"])

    def test_description_names_the_persist_argument_and_the_section(self):
        # LLM-facing: the description is how a caller learns the gap can be
        # closed by a command rather than by editing a file (#890).
        description = default_registry().get("get_models").description
        self.assertIn("persist", description)
        self.assertIn("[model_ids]", description)


class TestGetTasksCommand(unittest.TestCase):
    def test_defaults_to_empty_domain_and_limit_ten(self):
        client = MagicMock()
        tasks = client.__getitem__.return_value
        tasks.search.return_value.read.return_value = [{"name": "T"}]

        result = service(client=client).execute("get_tasks")

        client.__getitem__.assert_called_once_with("project.task")
        tasks.search.assert_called_once_with([], limit=10)
        tasks.search.return_value.read.assert_called_once_with(TASK_FIELDS)
        self.assertEqual(result, [{"name": "T"}])

    def test_passes_through_domain_and_limit(self):
        client = MagicMock()
        tasks = client.__getitem__.return_value
        domain = [("stage_id", "=", 3)]

        service(client=client).execute("get_tasks", domain=domain, limit=5)

        tasks.search.assert_called_once_with(domain, limit=5)

    def test_omitted_include_never_touches_the_detail_helper(self):
        # #630: without ``include`` the summary-only behavior is unchanged —
        # no per-task detail fetch happens at all.
        client = MagicMock()
        tasks = client.__getitem__.return_value
        tasks.search.return_value.read.return_value = [{"name": "T"}]

        with patch("odoo_dev_core.builtins.get_tasks.get_task_detail") as detail:
            result = service(client=client).execute("get_tasks")

        detail.assert_not_called()
        tasks.search.return_value.read.assert_called_once_with(TASK_FIELDS)
        self.assertEqual(result, [{"name": "T"}])

    def test_include_expands_each_result_via_get_task_detail(self):
        # #630: ``include`` reuses get_task's detail fetch per matched task,
        # so a batch of descriptions costs one get_tasks call.
        client = MagicMock()
        tasks = client.__getitem__.return_value
        tasks.search.return_value.ids = (7, 8)
        details = {
            7: {"task_id": 7, "description": "seven"},
            8: {"task_id": 8, "description": "eight"},
        }

        with patch(
            "odoo_dev_core.builtins.get_tasks.get_task_detail",
            side_effect=lambda client, task_id, include: details[task_id],
        ) as detail:
            result = service(client=client).execute(
                "get_tasks", include=["description"]
            )

        self.assertEqual(detail.call_count, 2)
        detail.assert_any_call(client, 7, include=["description"])
        detail.assert_any_call(client, 8, include=["description"])
        self.assertEqual(result, [details[7], details[8]])
        # The summary-only read path is bypassed entirely.
        tasks.search.return_value.read.assert_not_called()

    def test_include_path_still_honors_domain_and_limit(self):
        client = MagicMock()
        tasks = client.__getitem__.return_value
        tasks.search.return_value.ids = ()
        domain = [("stage_id", "=", 3)]

        result = service(client=client).execute(
            "get_tasks", domain=domain, limit=5, include=["description"]
        )

        tasks.search.assert_called_once_with(domain, limit=5)
        self.assertEqual(result, [])

    def test_include_drops_tasks_deleted_mid_batch(self):
        # A task deleted between the search and the per-task read yields None
        # from get_task_detail; the batch result must not contain holes.
        client = MagicMock()
        tasks = client.__getitem__.return_value
        tasks.search.return_value.ids = (7, 8)
        surviving = {"task_id": 8, "description": "eight"}

        with patch(
            "odoo_dev_core.builtins.get_tasks.get_task_detail",
            side_effect=lambda client, task_id, include: (
                surviving if task_id == 8 else None
            ),
        ):
            result = service(client=client).execute(
                "get_tasks", include=["description"]
            )

        self.assertEqual(result, [surviving])

    def test_empty_include_routes_through_the_detail_path(self):
        # ``include=[]`` mirrors get_task's contract: base identity fields
        # only, but shaped by the detail fetch — not the summary read.
        client = MagicMock()
        tasks = client.__getitem__.return_value
        tasks.search.return_value.ids = (7,)

        with patch(
            "odoo_dev_core.builtins.get_tasks.get_task_detail",
            return_value={"task_id": 7},
        ) as detail:
            result = service(client=client).execute("get_tasks", include=[])

        detail.assert_called_once_with(client, 7, include=[])
        self.assertEqual(result, [{"task_id": 7}])


class TestGetTodoCommand(unittest.TestCase):
    def test_returns_first_record_when_found(self):
        client = MagicMock()
        tasks = client.__getitem__.return_value
        tasks.search.return_value.read.return_value = [{"name": "T"}, {"name": "U"}]

        result = service(client=client).execute("get_todo", task_id=7)

        client.__getitem__.assert_called_once_with("project.task")
        tasks.search.assert_called_once_with([("id", "=", 7)], limit=1)
        self.assertEqual(result, {"name": "T"})

    def test_returns_none_when_missing(self):
        client = MagicMock()
        tasks = client.__getitem__.return_value
        tasks.search.return_value.read.return_value = []

        self.assertIsNone(service(client=client).execute("get_todo", task_id=99))


class TestCreateTaskCommand(unittest.TestCase):
    def test_creates_task_with_prefixed_name(self):
        client = MagicMock()
        tasks = client.__getitem__.return_value
        tasks.create.return_value = 1001

        result = service(client=client).execute(
            "create_task", name="Do it", project_id=4, description="details"
        )

        client.__getitem__.assert_called_once_with("project.task")
        tasks.create.assert_called_once_with(
            {"name": "[MCP] Do it", "project_id": 4, "description": "details"}
        )
        self.assertEqual(result, 1001)

    def test_description_defaults_to_empty(self):
        client = MagicMock()
        tasks = client.__getitem__.return_value

        service(client=client).execute("create_task", name="Quick", project_id=2)

        tasks.create.assert_called_once_with(
            {"name": "[MCP] Quick", "project_id": 2, "description": ""}
        )


#: The exact built-in command surface, pinned independently of the
#: decorator-recorded specs so a dropped ``@command`` (or an unimported builtin
#: module) fails loudly instead of silently shrinking the registry.
EXPECTED_BUILTIN_NAMES = frozenset(
    {
        "get_uid",
        "get_employee_id",
        "get_models",
        "get_tasks",
        "get_todo",
        "get_task",
        "get_task_chatter",
        "get_mail_status",
        "get_task_attachments",
        "read_attachment",
        "create_task",
        "search_chatter",
        "search_count",
        "search_knowledge_articles",
        "read_knowledge_article",
        "search_projects",
        "search_tasks",
        "normalize_timesheets",
        "assign_event",
        "task_note",
        "task_list",
        "task_aging",
        "task_question",
        "optimize_sessions",
        "query_sessions",
        "resync",
        "timesheet_summary",
        "unbilled_hours",
        "unlogged_time_report",
        "schedule_activity",
        "get_activities",
        "mark_activity_done",
        "search_activity_types",
        "log_event",
        "upload",
    }
)


class TestBuiltinCommandsExactSet(unittest.TestCase):
    """The default registry matches an explicit, hand-pinned id set."""

    def test_exact_command_id_set(self):
        self.assertEqual(set(default_registry().ids()), set(EXPECTED_BUILTIN_NAMES))

    def test_no_duplicate_or_missing_count(self):
        self.assertEqual(len(default_registry().ids()), len(EXPECTED_BUILTIN_NAMES))
        self.assertEqual(len(EXPECTED_BUILTIN_NAMES), 35)

    def test_get_task_and_chatter_are_builtins(self):
        ids = default_registry().ids()
        self.assertIn("get_task", ids)
        self.assertIn("get_task_chatter", ids)


if __name__ == "__main__":
    unittest.main()
