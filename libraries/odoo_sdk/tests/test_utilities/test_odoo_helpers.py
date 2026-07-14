import unittest
from datetime import date
from typing import Any
from unittest.mock import MagicMock, patch

from odoo_sdk.client import OdooClient
from odoo_sdk.transport.executor import OdooExecutor
from odoo_sdk.utilities.odoo_helpers import (
    _task_related_stages,
    _task_subtasks,
    _task_timesheets,
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


class _KeywordOnlyMessagePostExecutor(OdooExecutor):
    """Fake executor mimicking Odoo's positional-args / keyword-options split.

    ``execute`` forwards positional ``*args`` as the record arguments and
    ``**kwargs`` as the method options, exactly like ``execute_kw`` does. The
    stubbed ``message_post`` is keyword-only, so any trailing positional options
    dict (the #131 bug) reaches ``_message_post`` as a second positional argument
    and raises ``TypeError`` — reproducing the server-side crash locally.
    """

    def __init__(self) -> None:
        self.recorded: dict[str, Any] = {}

    def execute(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        if (model, method) != ("project.task", "message_post"):
            raise AssertionError(f"unexpected call: {model}.{method}")
        return self._message_post(*args, **kwargs)

    def _message_post(
        self,
        ids: list[int],
        *,
        body: str = "",
        message_type: str = "notification",
        subtype_xmlid: str | None = None,
    ) -> int:
        self.recorded = {
            "ids": ids,
            "body": body,
            "message_type": message_type,
            "subtype_xmlid": subtype_xmlid,
        }
        return 777


class _KeywordFieldsReadExecutor(OdooExecutor):
    """Fake executor mimicking Odoo's positional-ids / keyword-fields split for ``read``.

    ``execute`` forwards positional ``*args`` as the record arguments and
    ``**kwargs`` as the method options, exactly like ``execute_kw`` does. The
    stubbed ``read`` takes the record ids positionally and ``fields`` as a
    keyword-only argument, so a trailing positional ``{"fields": [...]}`` dict
    (the #166 bug) reaches ``_read`` as a second positional argument and raises
    ``TypeError`` — reproducing the server-side ``Invalid field 'fields'`` crash
    locally.
    """

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self.recorded: dict[str, Any] = {}

    def execute(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        if method != "read":
            raise AssertionError(f"unexpected call: {model}.{method}")
        return self._read(*args, **kwargs)

    def _read(self, ids: list[int], *, fields: list[str]) -> list[dict]:
        self.recorded = {"ids": ids, "fields": fields}
        return self._rows


class TestReadPassesFieldsAsKeyword(unittest.TestCase):
    """Regression for #166: the include ``read`` helpers must pass a raw id
    list positionally and ``fields`` as a keyword, not a trailing positional
    ``{"fields": [...]}`` dict that Odoo mis-reads as the positional ``fields``
    argument and crashes on.
    """

    def test_related_stages_reads_ids_with_keyword_fields(self):
        executor = _KeywordFieldsReadExecutor(
            [{"id": 5, "name": "Blocker", "stage_id": [1, "Todo"]}]
        )
        client = OdooClient(executor=executor)
        rows = _task_related_stages(client, [5])
        self.assertEqual(executor.recorded, {"ids": [5], "fields": ["name", "stage_id"]})
        self.assertEqual(rows, [[5, "Blocker", "Todo"]])

    def test_timesheets_reads_ids_with_keyword_fields(self):
        executor = _KeywordFieldsReadExecutor(
            [
                {
                    "id": 7,
                    "date": "2026-07-01",
                    "employee_id": [9, "Jane"],
                    "unit_amount": 2.5,
                    "name": "Work",
                }
            ]
        )
        client = OdooClient(executor=executor)
        rows = _task_timesheets(client, [7])
        self.assertEqual(
            executor.recorded,
            {"ids": [7], "fields": ["date", "employee_id", "unit_amount", "name"]},
        )
        self.assertEqual(
            rows,
            [{"date": "2026-07-01", "employee": "Jane", "hours": 2.5, "name": "Work"}],
        )

    def test_subtasks_reads_ids_with_keyword_fields(self):
        executor = _KeywordFieldsReadExecutor(
            [{"id": 11, "name": "Sub", "stage_id": [3, "Done"], "user_ids": [4]}]
        )
        client = OdooClient(executor=executor)
        rows = _task_subtasks(client, [11])
        self.assertEqual(
            executor.recorded,
            {"ids": [11], "fields": ["name", "stage_id", "user_ids"]},
        )
        self.assertEqual(
            rows,
            [{"id": 11, "name": "Sub", "stage": "Done", "assignees": [4]}],
        )


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


class _CreateSemanticsExecutor(OdooExecutor):
    """Fake executor mimicking Odoo's ``create`` list-vs-dict semantics.

    Odoo's ORM ``create`` returns a *list* of ids for a batch (list-of-dicts)
    call and a *scalar* id for a single-dict call. ``create_timesheet`` must
    issue the single-dict form so the id survives the SQLite bind in
    ``db.create_run`` (a list argument raises "type 'list' is not
    supported" — issue #167). This executor reproduces that split locally.
    """

    def __init__(self) -> None:
        self.recorded_vals: Any = None

    def execute(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        if (model, method) != ("account.analytic.line", "create"):
            raise AssertionError(f"unexpected call: {model}.{method}")
        (vals,) = args
        self.recorded_vals = vals
        # Batch create (list of dicts) yields a list of ids; single create
        # (a dict) yields a scalar id.
        if isinstance(vals, list):
            return [123]
        return 123


class TestCreateTimesheet(unittest.TestCase):
    def test_creates_and_returns_id(self):
        client = _client()
        client.execute.return_value = 99
        result = create_timesheet(client, task_id=1, project_id=2, employee_id=3, today=date(2024, 6, 1))
        client.execute.assert_called_once_with(
            "account.analytic.line",
            "create",
            {
                "name": "[/] Work in progress",
                "unit_amount": 0.0,
                "project_id": 2,
                "task_id": 1,
                "date": "2024-06-01",
                "employee_id": 3,
            },
        )
        self.assertEqual(result, 99)

    def test_returns_scalar_id_not_list(self):
        # Regression for #167: passing a list-of-one-dict is a *batch* create,
        # which Odoo answers with ``[id]`` (a list). A list then breaks the
        # SQLite bind in ``db.create_run`` ("type 'list' is not supported").
        # ``create_timesheet`` must send a single dict so the result is a scalar
        # int. This test fails on the pre-fix (list-wrapped) implementation.
        executor = _CreateSemanticsExecutor()
        client = OdooClient(executor=executor)
        result = create_timesheet(
            client, task_id=1, project_id=2, employee_id=3, today=date(2024, 6, 1)
        )
        self.assertIsInstance(executor.recorded_vals, dict)
        self.assertIsInstance(result, int)
        self.assertEqual(result, 123)


class TestUpdateTimesheet(unittest.TestCase):
    def test_writes_amount_and_description(self):
        client = _client()
        update_timesheet(client, timesheet_id=50, unit_amount=1.5, description="[/] Done")
        client.execute.assert_called_once_with(
            "account.analytic.line",
            "write",
            [50],
            {"unit_amount": 1.5, "name": "[/] Done"},
        )


class TestPostChatterNote(unittest.TestCase):
    def test_passes_options_as_keyword_arguments(self):
        # Regression for #131: Odoo's ``message_post`` is keyword-only, so the
        # options must be forwarded as ``execute`` keyword arguments. The record
        # ids stay the only positional RPC argument; there must be NO trailing
        # positional options dict (that would land as a positional method arg on
        # the server and crash with ``TypeError``).
        client = _client()
        client.execute.return_value = 777
        result = post_chatter_note(client, task_id=5, body="Hello")
        # Body is rendered from Markdown to HTML before posting (issue #324).
        client.execute.assert_called_once_with(
            "project.task",
            "message_post",
            [5],
            body="<p>Hello</p>",
            message_type="comment",
            subtype_xmlid="mail.mt_note",
        )
        # No positional options dict may sneak in after the record ids.
        pos_args = client.execute.call_args.args
        self.assertEqual(pos_args, ("project.task", "message_post", [5]))
        self.assertEqual(result, 777)

    def test_drives_keyword_only_message_post_without_type_error(self):
        # Stronger reproduction of #131: a fake executor that mimics Odoo's
        # split of positional record args vs. keyword method options. Its
        # ``message_post`` is keyword-only, so a trailing positional options
        # dict (the old bug) raises ``TypeError``. This test would fail against
        # the pre-fix implementation and passes only when the options are keyword.
        executor = _KeywordOnlyMessagePostExecutor()
        client = OdooClient(executor=executor)
        result = post_chatter_note(client, task_id=5, body="Hello")
        self.assertEqual(result, 777)
        self.assertEqual(
            executor.recorded,
            {
                "ids": [5],
                "body": "<p>Hello</p>",
                "message_type": "comment",
                "subtype_xmlid": "mail.mt_note",
            },
        )

    def test_markdown_body_is_rendered_to_html(self):
        # Regression for #324: a Markdown body must reach ``message_post`` as
        # HTML so it renders formatted in the chatter instead of as literal
        # Markdown text with collapsed newlines.
        client = _client()
        client.execute.return_value = 1
        post_chatter_note(
            client, task_id=5, body="**Summary**\n\n- one\n- two"
        )
        body = client.execute.call_args.kwargs["body"]
        self.assertIn("<strong>Summary</strong>", body)
        self.assertIn("<ul>", body)
        self.assertIn("<li>one</li>", body)


class _RecordingExecutor(OdooExecutor):
    """Fake executor that records every ``execute`` call and services ``read``.

    Real ``OdooClient`` execution runs through this so the system-wide
    ``forbid_unlink`` guard is exercised: an ``unlink`` would raise, and any
    call the code issues is captured in ``calls`` for assertions.
    """

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self.calls: list[tuple[str, str, tuple[Any, ...], dict[str, Any]]] = []

    def execute(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((model, method, args, kwargs))
        if method == "read":
            return self._rows
        return None


class TestMergeTimesheets(unittest.TestCase):
    def test_sums_hours_and_joins_descriptions(self):
        client = _client()
        client.execute.side_effect = [
            # read
            [
                {"id": 1, "unit_amount": 1.0, "name": "[/] Work A"},
                {"id": 2, "unit_amount": 0.5, "name": "[/] Work B"},
            ],
            None,  # primary write
            None,  # zero-out write on merged-in rows
        ]
        merge_timesheets(client, primary_id=1, ids_to_merge=[2])
        # Merge is read + primary write + zero-out write of the merged-in rows;
        # the merged-in rows are kept in place (no ``unlink``).
        self.assertEqual(client.execute.call_count, 3)
        write_call = client.execute.call_args_list[1]
        self.assertEqual(write_call.args[0], "account.analytic.line")
        self.assertEqual(write_call.args[1], "write")
        vals = write_call.args[3]
        self.assertAlmostEqual(vals["unit_amount"], 1.5)
        self.assertIn("Work A", vals["name"])
        self.assertIn("Work B", vals["name"])

    def test_zeros_merged_rows_and_never_unlinks(self):
        # #185 regression: merged-in rows must contribute 0 hours afterwards
        # WITHOUT deletion. The primary keeps the summed hours, each source row
        # is zeroed via a single ``write``, and no ``unlink`` is ever issued.
        executor = _RecordingExecutor(
            [
                {"id": 1, "unit_amount": 1.0, "name": "[/] Work A"},
                {"id": 2, "unit_amount": 0.5, "name": "[/] Work B"},
                {"id": 3, "unit_amount": 0.25, "name": "[/] Work C"},
            ]
        )
        client = OdooClient(executor=executor)
        merge_timesheets(client, primary_id=1, ids_to_merge=[2, 3])

        methods = [method for _, method, _, _ in executor.calls]
        self.assertNotIn("unlink", methods)

        primary_write = executor.calls[1]
        self.assertEqual(primary_write[0], "account.analytic.line")
        self.assertEqual(primary_write[1], "write")
        self.assertEqual(primary_write[2][0], [1])
        self.assertAlmostEqual(primary_write[2][1]["unit_amount"], 1.75)

        zero_write = executor.calls[2]
        self.assertEqual(zero_write[0], "account.analytic.line")
        self.assertEqual(zero_write[1], "write")
        self.assertEqual(zero_write[2][0], [2, 3])
        self.assertEqual(zero_write[2][1]["unit_amount"], 0.0)

    def test_no_merge_when_no_others(self):
        client = _client()
        client.execute.side_effect = [
            [{"id": 1, "unit_amount": 2.0, "name": "[/] Solo"}],
            None,  # write
        ]
        merge_timesheets(client, primary_id=1, ids_to_merge=[])
        # Empty ids_to_merge: only read + primary write; no zero-out write.
        self.assertEqual(client.execute.call_count, 2)

    def test_skips_in_progress_descriptions(self):
        client = _client()
        client.execute.side_effect = [
            [
                {"id": 1, "unit_amount": 1.0, "name": "[/] Work in progress"},
                {"id": 2, "unit_amount": 0.5, "name": "[/] Real work"},
            ],
            None,  # primary write
            None,  # zero-out write
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
