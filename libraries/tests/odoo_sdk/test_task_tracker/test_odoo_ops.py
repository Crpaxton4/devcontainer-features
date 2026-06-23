import unittest
from datetime import date
from unittest.mock import MagicMock

from odoo_sdk.task_tracker.odoo_ops import (
    create_timesheet,
    get_employee_id,
    merge_timesheets,
    name_search_projects,
    name_search_tasks,
    post_chatter_note,
    update_timesheet,
)


def _client() -> MagicMock:
    return MagicMock()


class TestNameSearchProjects(unittest.TestCase):
    def test_returns_id_name_dicts(self):
        client = _client()
        client.execute.return_value = [(1, "Accounting"), (2, "HR")]
        result = name_search_projects(client, "acc", limit=5)
        client.execute.assert_called_once_with(
            "project.project", "name_search", "acc", [], "ilike", 5
        )
        self.assertEqual(result, [{"id": 1, "name": "Accounting"}, {"id": 2, "name": "HR"}])

    def test_empty_results(self):
        client = _client()
        client.execute.return_value = []
        self.assertEqual(name_search_projects(client, "xyz"), [])


class TestNameSearchTasks(unittest.TestCase):
    def test_filters_by_project_id(self):
        client = _client()
        client.execute.return_value = [(10, "Fix bug")]
        result = name_search_tasks(client, "fix", project_id=3, limit=10)
        client.execute.assert_called_once_with(
            "project.task",
            "name_search",
            "fix",
            [("project_id", "=", 3)],
            "ilike",
            10,
        )
        self.assertEqual(result, [{"id": 10, "name": "Fix bug"}])


class TestGetEmployeeId(unittest.TestCase):
    def test_returns_employee_id(self):
        client = _client()
        client.execute.return_value = [{"id": 42}]
        result = get_employee_id(client, uid=7)
        client.execute.assert_called_once_with(
            "hr.employee",
            "search_read",
            [[("user_id", "=", 7)]],
            {"fields": ["id"], "limit": 1},
        )
        self.assertEqual(result, 42)

    def test_raises_when_no_employee(self):
        client = _client()
        client.execute.return_value = []
        with self.assertRaises(RuntimeError) as ctx:
            get_employee_id(client, uid=7)
        self.assertIn("hr.employee", str(ctx.exception))


class TestCreateTimesheet(unittest.TestCase):
    def test_creates_and_returns_id(self):
        client = _client()
        client.execute.return_value = 99
        result = create_timesheet(client, task_id=1, project_id=2, employee_id=3, today=date(2024, 6, 1))
        client.execute.assert_called_once_with(
            "account.analytic.line",
            "create",
            [{
                "name": "[/] Work in progress",
                "unit_amount": 0.0,
                "project_id": 2,
                "task_id": 1,
                "date": "2024-06-01",
                "employee_id": 3,
            }],
        )
        self.assertEqual(result, 99)


class TestUpdateTimesheet(unittest.TestCase):
    def test_writes_amount_and_description(self):
        client = _client()
        update_timesheet(client, timesheet_id=50, unit_amount=1.5, description="[/] Done")
        client.execute.assert_called_once_with(
            "account.analytic.line",
            "write",
            [[50]],
            {"unit_amount": 1.5, "name": "[/] Done"},
        )


class TestPostChatterNote(unittest.TestCase):
    def test_calls_message_post_and_returns_id(self):
        client = _client()
        client.execute.return_value = 777
        result = post_chatter_note(client, task_id=5, body="Hello")
        client.execute.assert_called_once_with(
            "project.task",
            "message_post",
            [5],
            {
                "body": "Hello",
                "message_type": "comment",
                "subtype_xmlid": "mail.mt_note",
            },
        )
        self.assertEqual(result, 777)


class TestMergeTimesheets(unittest.TestCase):
    def test_sums_hours_and_joins_descriptions(self):
        client = _client()
        client.execute.side_effect = [
            # read
            [
                {"id": 1, "unit_amount": 1.0, "name": "[/] Work A"},
                {"id": 2, "unit_amount": 0.5, "name": "[/] Work B"},
            ],
            None,  # write
            None,  # unlink
        ]
        merge_timesheets(client, primary_id=1, ids_to_merge=[2])
        # Second call is write
        write_call = client.execute.call_args_list[1]
        self.assertEqual(write_call.args[0], "account.analytic.line")
        self.assertEqual(write_call.args[1], "write")
        vals = write_call.args[3]
        self.assertAlmostEqual(vals["unit_amount"], 1.5)
        self.assertIn("Work A", vals["name"])
        self.assertIn("Work B", vals["name"])
        # Third call is unlink
        unlink_call = client.execute.call_args_list[2]
        self.assertEqual(unlink_call.args[1], "unlink")

    def test_no_merge_when_no_others(self):
        client = _client()
        client.execute.side_effect = [
            [{"id": 1, "unit_amount": 2.0, "name": "[/] Solo"}],
            None,  # write
        ]
        merge_timesheets(client, primary_id=1, ids_to_merge=[])
        # Only 2 calls: read + write; unlink should not be called
        self.assertEqual(client.execute.call_count, 2)

    def test_skips_in_progress_descriptions(self):
        client = _client()
        client.execute.side_effect = [
            [
                {"id": 1, "unit_amount": 1.0, "name": "[/] Work in progress"},
                {"id": 2, "unit_amount": 0.5, "name": "[/] Real work"},
            ],
            None,
            None,
        ]
        merge_timesheets(client, primary_id=1, ids_to_merge=[2])
        write_call = client.execute.call_args_list[1]
        merged_name = write_call.args[3]["name"]
        self.assertNotIn("Work in progress", merged_name)
        self.assertIn("Real work", merged_name)


if __name__ == "__main__":
    unittest.main()
