import unittest
from datetime import date
from unittest.mock import MagicMock, patch

from odoo_sdk.utilities.odoo_helpers import (
    create_timesheet,
    get_employee_id,
    get_task_chatter,
    get_task_detail,
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
            [("user_id", "=", 7)],
            fields=["id"],
            limit=1,
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


class TestGetTaskChatter(unittest.TestCase):
    def _make_message(self, body="<p>Hello</p>"):
        return {
            "id": 1,
            "date": "2026-06-20T10:30:00",
            "author_id": [5, "Jane Smith"],
            "message_type": "comment",
            "subtype_id": [1, "Discussions"],
            "body": body,
        }

    def test_correct_search_read_call(self):
        client = _client()
        client.execute.return_value = []
        get_task_chatter(client, task_id=42)
        client.execute.assert_called_once_with(
            "mail.message",
            "search_read",
            [("model", "=", "project.task"), ("res_id", "=", 42)],
            fields=["id", "date", "author_id", "message_type", "subtype_id", "body"],
            order="date asc",
            limit=100,
        )

    def test_respects_custom_limit(self):
        client = _client()
        client.execute.return_value = []
        get_task_chatter(client, task_id=1, limit=5)
        self.assertEqual(client.execute.call_args.kwargs["limit"], 5)

    def test_extracts_display_names_from_tuples(self):
        client = _client()
        client.execute.return_value = [self._make_message()]
        with patch("odoo_sdk.utilities.odoo_helpers._html_to_markdown", return_value="Hello"):
            result = get_task_chatter(client, task_id=1)
        self.assertEqual(result[0]["author"], "Jane Smith")
        self.assertEqual(result[0]["subtype"], "Discussions")

    def test_body_converted_not_raw_html(self):
        client = _client()
        client.execute.return_value = [self._make_message(body="<p>Hello</p>")]
        result = get_task_chatter(client, task_id=1)
        self.assertNotIn("<p>", result[0]["body"])
        self.assertIn("Hello", result[0]["body"])

    def test_empty_body_returns_empty_string(self):
        client = _client()
        msg = self._make_message(body="")
        client.execute.return_value = [msg]
        result = get_task_chatter(client, task_id=1)
        self.assertEqual(result[0]["body"], "")

    def test_returns_empty_list_when_no_messages(self):
        client = _client()
        client.execute.return_value = []
        self.assertEqual(get_task_chatter(client, task_id=99), [])


class TestGetTaskDetail(unittest.TestCase):
    def _make_record(self):
        return {
            "id": 42,
            "name": "Implement Feature X",
            "description": "<h2>Overview</h2><p>Do the thing.</p>",
            "project_id": [1, "My Project"],
            "stage_id": [3, "In Progress"],
            "user_ids": [7, 8],
            "date_deadline": "2026-07-15",
            "priority": "1",
            "tag_ids": [],
        }

    def test_correct_search_read_call(self):
        client = _client()
        client.execute.return_value = [self._make_record()]
        get_task_detail(client, task_id=42)
        client.execute.assert_called_once_with(
            "project.task",
            "search_read",
            [("id", "=", 42)],
            fields=[
                "name",
                "project_id",
                "stage_id",
                "user_ids",
                "date_deadline",
                "priority",
                "tag_ids",
                "description",
            ],
            limit=1,
        )

    def test_default_is_description_only_no_relation_fields(self):
        client = _client()
        client.execute.return_value = [self._make_record()]
        result = get_task_detail(client, task_id=42)
        # Only the single search_read; no follow-up reads for relations.
        client.execute.assert_called_once()
        requested = client.execute.call_args.kwargs["fields"]
        for relation_field in (
            "depend_on_ids",
            "dependent_ids",
            "timesheet_ids",
            "child_ids",
        ):
            self.assertNotIn(relation_field, requested)
        self.assertIn("description", result)
        for absent in ("blocked_by", "blocks", "timesheets", "subtasks", "chatter"):
            self.assertNotIn(absent, result)

    def test_empty_include_omits_description(self):
        client = _client()
        client.execute.return_value = [self._make_record()]
        result = get_task_detail(client, task_id=42, include=[])
        self.assertNotIn("description", result)
        self.assertNotIn("description", client.execute.call_args.kwargs["fields"])
        # Base identity fields still present.
        self.assertEqual(result["name"], "Implement Feature X")
        self.assertEqual(result["project"], "My Project")

    def test_dependencies_hydrated_only_when_requested(self):
        client = _client()
        record = self._make_record()
        record["depend_on_ids"] = [100]
        record["dependent_ids"] = [200]
        client.execute.side_effect = [
            [record],
            [{"id": 100, "name": "Blocker", "stage_id": [1, "Todo"]}],
            [{"id": 200, "name": "Blocked", "stage_id": [2, "Doing"]}],
        ]
        result = get_task_detail(client, task_id=42, include=["dependencies"])
        requested = client.execute.call_args_list[0].kwargs["fields"]
        self.assertIn("depend_on_ids", requested)
        self.assertIn("dependent_ids", requested)
        self.assertEqual(result["blocked_by"], [[100, "Blocker", "Todo"]])
        self.assertEqual(result["blocks"], [[200, "Blocked", "Doing"]])

    def test_timesheets_hydrated_only_when_requested(self):
        client = _client()
        record = self._make_record()
        record["timesheet_ids"] = [7]
        client.execute.side_effect = [
            [record],
            [
                {
                    "id": 7,
                    "date": "2026-07-01",
                    "employee_id": [9, "Jane"],
                    "unit_amount": 2.5,
                    "name": "Work",
                }
            ],
        ]
        result = get_task_detail(client, task_id=42, include=["timesheets"])
        self.assertIn("timesheet_ids", client.execute.call_args_list[0].kwargs["fields"])
        self.assertEqual(
            result["timesheets"],
            [{"date": "2026-07-01", "employee": "Jane", "hours": 2.5, "name": "Work"}],
        )

    def test_subtasks_hydrated_only_when_requested(self):
        client = _client()
        record = self._make_record()
        record["child_ids"] = [11]
        client.execute.side_effect = [
            [record],
            [{"id": 11, "name": "Sub", "stage_id": [3, "Done"], "user_ids": [4]}],
        ]
        result = get_task_detail(client, task_id=42, include=["subtasks"])
        self.assertIn("child_ids", client.execute.call_args_list[0].kwargs["fields"])
        self.assertEqual(
            result["subtasks"],
            [{"id": 11, "name": "Sub", "stage": "Done", "assignees": [4]}],
        )

    def test_dependencies_skip_missing_related_records(self):
        client = _client()
        record = self._make_record()
        record["depend_on_ids"] = [100, 101]
        record["dependent_ids"] = []
        client.execute.side_effect = [
            [record],
            # 101 is absent from the read result (e.g. deleted mid-flight).
            [{"id": 100, "name": "Blocker", "stage_id": [1, "Todo"]}],
        ]
        result = get_task_detail(client, task_id=42, include=["dependencies"])
        self.assertEqual(result["blocked_by"], [[100, "Blocker", "Todo"]])
        self.assertEqual(result["blocks"], [])

    def test_empty_relations_skip_followup_reads(self):
        client = _client()
        record = self._make_record()
        record["depend_on_ids"] = []
        record["dependent_ids"] = []
        record["timesheet_ids"] = []
        record["child_ids"] = []
        client.execute.return_value = [record]
        result = get_task_detail(
            client,
            task_id=42,
            include=["dependencies", "timesheets", "subtasks"],
        )
        # No follow-up reads when the relation lists are empty.
        client.execute.assert_called_once()
        self.assertEqual(result["blocked_by"], [])
        self.assertEqual(result["blocks"], [])
        self.assertEqual(result["timesheets"], [])
        self.assertEqual(result["subtasks"], [])

    def test_returns_none_when_not_found(self):
        client = _client()
        client.execute.return_value = []
        self.assertIsNone(get_task_detail(client, task_id=999))

    def test_flattens_many2one_display_names(self):
        client = _client()
        client.execute.return_value = [self._make_record()]
        with patch("odoo_sdk.utilities.odoo_helpers._html_to_markdown", return_value="desc"):
            result = get_task_detail(client, task_id=42)
        self.assertEqual(result["project"], "My Project")
        self.assertEqual(result["stage"], "In Progress")

    def test_description_html_converted(self):
        client = _client()
        client.execute.return_value = [self._make_record()]
        result = get_task_detail(client, task_id=42)
        self.assertNotIn("<h2>", result["description"])
        self.assertIn("Overview", result["description"])

    def test_task_id_in_result(self):
        client = _client()
        client.execute.return_value = [self._make_record()]
        with patch("odoo_sdk.utilities.odoo_helpers._html_to_markdown", return_value=""):
            result = get_task_detail(client, task_id=42)
        self.assertEqual(result["task_id"], 42)


if __name__ == "__main__":
    unittest.main()
