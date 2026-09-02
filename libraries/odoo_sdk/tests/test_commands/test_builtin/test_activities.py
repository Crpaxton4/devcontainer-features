"""Tests for the ``mail.activity`` tool family (issue #677).

The helpers are driven through a real :class:`OdooClient` wrapping a recording
fake executor, so the exact domains, fields, and create values that would reach
Odoo are asserted and the whole flow — type-name resolution, the mandatory
``ir.model`` lookup, Markdown/HTML round-tripping, and the read-before-delete
ordering of ``action_feedback`` — is exercised offline. Nothing here touches a
live Odoo instance, and none of it has been verified against one.
"""

import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from odoo_sdk.client import OdooClient
from odoo_sdk.commands.builtin import BUILTIN_COMMANDS
from odoo_sdk.commands.builtin.get_activities import GetActivitiesCommand
from odoo_sdk.commands.builtin.mark_activity_done import MarkActivityDoneCommand
from odoo_sdk.commands.builtin.schedule_activity import ScheduleActivityCommand
from odoo_sdk.commands.builtin.search_activity_types import (
    SearchActivityTypesCommand,
)
from odoo_sdk.transport.errors import OdooAccessError
from odoo_sdk.transport.executor import OdooExecutor
from odoo_sdk.utilities.activities import (
    DEFAULT_ACTIVITY_RES_MODEL,
    MODEL_LOOKUP_DENIED_MESSAGE,
    get_activities,
    mark_activity_done,
    resolve_activity_type_id,
    schedule_activity,
    search_activity_types,
)

_ACTIVITY_FIELDS = [
    "id",
    "res_model",
    "res_id",
    "res_name",
    "activity_type_id",
    "summary",
    "note",
    "date_deadline",
    "user_id",
    "create_date",
    "state",
]

_TYPE_FIELDS = ["id", "name", "res_model"]


def _activity(
    activity_id: int = 1,
    *,
    type_pair: Any = (4, "To Do"),
    note: str = "<p>Chase the client</p>",
    **overrides: Any,
) -> dict:
    """Build a raw ``mail.activity`` row as Odoo would return it."""
    row = {
        "id": activity_id,
        "res_model": "project.task",
        "res_id": 42,
        "res_name": "Fix VAT",
        # Odoo serializes an unset many2one as ``False``, a set one as [id, name].
        "activity_type_id": list(type_pair) if type_pair else type_pair,
        "summary": "Follow up",
        "note": note,
        "date_deadline": "2026-09-10",
        "user_id": [7, "Chris P"],
        "create_date": "2026-09-02 09:00:00",
        "state": "planned",
    }
    row.update(overrides)
    return row


class _RecordingExecutor(OdooExecutor):
    """Fake executor recording every call and dispatching canned data by model.

    Real ``OdooClient`` execution runs through this (including the system-wide
    ``forbid_unlink`` guard), and every issued call is captured in ``calls`` so
    the exact domains / fields / create values can be asserted. ``deny_ir_model``
    models a least-privileged account that cannot read ``ir.model`` — the exact
    failure that makes activity creation impossible.
    """

    def __init__(
        self,
        *,
        model_rows: list[dict] | None = None,
        type_rows: list[list[dict]] | None = None,
        activities: list[dict] | None = None,
        created_id: int = 1,
        feedback_result: Any = 900,
        deny_ir_model: bool = False,
    ) -> None:
        self._model_rows = model_rows if model_rows is not None else [{"id": 71}]
        # One canned response per ``mail.activity.type`` search, consumed in
        # order so the resolver's exact-then-substring two-pass is observable.
        self._type_rows = list(type_rows) if type_rows is not None else []
        self._activities = activities if activities is not None else [_activity()]
        self._created_id = created_id
        self._feedback_result = feedback_result
        self._deny_ir_model = deny_ir_model
        self.calls: list[tuple[str, str, tuple[Any, ...], dict[str, Any]]] = []

    def _next_type_rows(self) -> list[dict]:
        return self._type_rows.pop(0) if self._type_rows else []

    def execute(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((model, method, args, kwargs))
        if model == "ir.model":
            if self._deny_ir_model:
                raise OdooAccessError("You are not allowed to access 'Models'.")
            return self._model_rows
        if model == "mail.activity.type":
            return self._next_type_rows()
        if (model, method) == ("mail.activity", "create"):
            return self._created_id
        if (model, method) in {
            ("mail.activity", "read"),
            ("mail.activity", "search_read"),
        }:
            return self._activities
        if (model, method) == ("mail.activity", "action_feedback"):
            return self._feedback_result
        raise AssertionError(f"unexpected call: {model}.{method}")


def _client(uid: int = 7, **kwargs: Any) -> tuple[OdooClient, _RecordingExecutor]:
    executor = _RecordingExecutor(**kwargs)
    client = OdooClient(executor=executor)
    # ``schedule_activity``/``get_activities`` default to the authenticated user,
    # so the fake executor must answer the uid probe the client delegates to it.
    executor.uid = uid  # type: ignore[attr-defined]
    return client, executor


def _calls_to(executor: _RecordingExecutor, model: str, method: str) -> list[tuple]:
    return [call for call in executor.calls if call[:2] == (model, method)]


class TestResolveActivityTypeId(unittest.TestCase):
    """A type is addressable by raw id or by name (issue #677's resolver)."""

    def test_int_passes_straight_through_without_any_query(self):
        client, executor = _client()
        self.assertEqual(resolve_activity_type_id(client, 4), 4)
        # An id needs no lookup at all — zero RPC calls were issued.
        self.assertEqual(executor.calls, [])

    def test_exact_name_match_wins_on_the_first_pass(self):
        client, executor = _client(type_rows=[[{"id": 4, "name": "Call"}]])
        self.assertEqual(resolve_activity_type_id(client, "call"), 4)
        searches = _calls_to(executor, "mail.activity.type", "search_read")
        # A single =ilike pass; the substring fallback never ran.
        self.assertEqual(len(searches), 1)
        self.assertEqual(searches[0][2][0], [("name", "=ilike", "call")])
        self.assertEqual(searches[0][3]["fields"], _TYPE_FIELDS)

    def test_substring_fallback_runs_only_when_exact_finds_nothing(self):
        client, executor = _client(type_rows=[[], [{"id": 9, "name": "Call back"}]])
        self.assertEqual(resolve_activity_type_id(client, "back"), 9)
        searches = _calls_to(executor, "mail.activity.type", "search_read")
        self.assertEqual(len(searches), 2)
        self.assertEqual(searches[1][2][0], [("name", "ilike", "back")])

    def test_res_model_narrows_to_generic_plus_scoped_types(self):
        client, executor = _client(type_rows=[[{"id": 4, "name": "Call"}]])
        resolve_activity_type_id(client, "Call", res_model="project.task")
        domain = _calls_to(executor, "mail.activity.type", "search_read")[0][2][0]
        self.assertEqual(
            domain,
            [
                ("name", "=ilike", "Call"),
                "|",
                ("res_model", "=", False),
                ("res_model", "=", "project.task"),
            ],
        )

    def test_unknown_name_error_lists_the_available_types(self):
        # exact -> none, substring -> none, then the "what is available" listing.
        client, _ = _client(
            type_rows=[[], [], [{"id": 4, "name": "To Do"}, {"id": 5, "name": "Call"}]]
        )
        with self.assertRaises(ValueError) as ctx:
            resolve_activity_type_id(client, "Todo")
        message = str(ctx.exception)
        self.assertIn("Todo", message)
        self.assertIn("To Do", message)
        self.assertIn("Call", message)

    def test_unknown_name_with_no_types_at_all_omits_the_listing(self):
        client, _ = _client(type_rows=[[], [], []])
        with self.assertRaises(ValueError) as ctx:
            resolve_activity_type_id(client, "Todo")
        self.assertEqual(str(ctx.exception), "No activity type matches 'Todo'.")

    def test_ambiguous_name_names_every_candidate(self):
        client, _ = _client(
            type_rows=[[], [{"id": 4, "name": "Call"}, {"id": 9, "name": "Call back"}]]
        )
        with self.assertRaises(ValueError) as ctx:
            resolve_activity_type_id(client, "Call")
        message = str(ctx.exception)
        self.assertIn("ambiguous", message)
        self.assertIn("id=4", message)
        self.assertIn("id=9", message)

    def test_blank_name_is_rejected_before_any_query(self):
        client, executor = _client()
        with self.assertRaises(ValueError) as ctx:
            resolve_activity_type_id(client, "   ")
        self.assertIn("non-empty name", str(ctx.exception))
        self.assertEqual(executor.calls, [])

    def test_bool_is_not_treated_as_an_id(self):
        # ``bool`` subclasses ``int``; letting True through would schedule
        # against activity type 1 rather than reporting the bad input.
        client, _ = _client(type_rows=[[], [], []])
        with self.assertRaises(ValueError):
            resolve_activity_type_id(client, True)


class TestSearchActivityTypes(unittest.TestCase):
    """The discovery counterpart is read-only and normalises ``res_model``."""

    def test_returns_id_name_and_normalised_res_model(self):
        client, _ = _client(
            type_rows=[
                [
                    {"id": 4, "name": "To Do", "res_model": False},
                    {"id": 9, "name": "Upsell", "res_model": "crm.lead"},
                ]
            ]
        )
        self.assertEqual(
            search_activity_types(client),
            [
                {"id": 4, "name": "To Do", "res_model": None},
                {"id": 9, "name": "Upsell", "res_model": "crm.lead"},
            ],
        )

    def test_query_becomes_an_ilike_name_predicate(self):
        client, executor = _client(type_rows=[[]])
        search_activity_types(client, query="meet")
        domain = _calls_to(executor, "mail.activity.type", "search_read")[0][2][0]
        self.assertEqual(domain, [("name", "ilike", "meet")])

    def test_no_query_searches_every_type(self):
        client, executor = _client(type_rows=[[]])
        search_activity_types(client)
        _, _, args, kwargs = _calls_to(executor, "mail.activity.type", "search_read")[0]
        self.assertEqual(args[0], [])
        self.assertEqual(kwargs["order"], "name asc, id asc")
        self.assertEqual(kwargs["limit"], 20)


class TestScheduleActivity(unittest.TestCase):
    """Creating an activity resolves the model id, the type, and the assignee."""

    def _client(self, **kwargs: Any):
        kwargs.setdefault("type_rows", [[{"id": 4, "name": "To Do"}]])
        return _client(**kwargs)

    def _create_values(self, executor: _RecordingExecutor) -> dict:
        return _calls_to(executor, "mail.activity", "create")[0][2][0]

    def test_defaults_to_project_task_and_the_current_uid(self):
        client, executor = self._client()
        schedule_activity(client, 42)
        model_domain = _calls_to(executor, "ir.model", "search_read")[0][2][0]
        self.assertEqual(model_domain, [("model", "=", "project.task")])
        values = self._create_values(executor)
        self.assertEqual(values["res_model_id"], 71)
        self.assertEqual(values["res_id"], 42)
        self.assertEqual(values["user_id"], 7)
        self.assertEqual(DEFAULT_ACTIVITY_RES_MODEL, "project.task")

    def test_res_model_id_is_sent_and_res_model_is_not(self):
        # ``mail.activity.res_model`` is a read-only related mirror of
        # ``res_model_id``; writing it would be rejected or ignored.
        client, executor = self._client()
        schedule_activity(client, 42, res_model="crm.lead")
        values = self._create_values(executor)
        self.assertIn("res_model_id", values)
        self.assertNotIn("res_model", values)

    def test_activity_type_name_is_resolved_to_an_id(self):
        client, executor = self._client()
        schedule_activity(client, 42, activity_type="to do")
        self.assertEqual(self._create_values(executor)["activity_type_id"], 4)

    def test_optional_values_are_omitted_so_odoo_defaults_apply(self):
        client, executor = self._client(type_rows=[])
        schedule_activity(client, 42)
        values = self._create_values(executor)
        for key in ("activity_type_id", "summary", "note", "date_deadline"):
            self.assertNotIn(key, values)

    def test_summary_is_forwarded_verbatim(self):
        client, executor = self._client(type_rows=[])
        schedule_activity(client, 42, summary="Chase the client")
        self.assertEqual(self._create_values(executor)["summary"], "Chase the client")

    def test_note_is_rendered_from_markdown_to_html(self):
        client, executor = self._client(type_rows=[])
        schedule_activity(client, 42, note="**chase** the client")
        note = self._create_values(executor)["note"]
        self.assertIn("<strong>chase</strong>", note)

    def test_explicit_assignee_and_deadline_are_forwarded(self):
        client, executor = self._client(type_rows=[])
        schedule_activity(client, 42, date_deadline="2026-09-10", user_id=12)
        values = self._create_values(executor)
        self.assertEqual(values["date_deadline"], "2026-09-10")
        self.assertEqual(values["user_id"], 12)

    def test_malformed_deadline_is_rejected_before_any_call(self):
        client, executor = self._client()
        with self.assertRaises(ValueError) as ctx:
            schedule_activity(client, 42, date_deadline="10/09/2026")
        self.assertIn("date_deadline", str(ctx.exception))
        self.assertEqual(executor.calls, [])

    def test_created_activity_is_read_back_and_shaped(self):
        client, _ = self._client(created_id=1)
        result = schedule_activity(client, 42, activity_type="To Do")
        self.assertEqual(result["activity_id"], 1)
        self.assertEqual(result["activity_type_id"], 4)
        self.assertEqual(result["activity_type"], "To Do")
        self.assertEqual(result["user_id"], 7)
        self.assertEqual(result["user"], "Chris P")
        # The HTML note comes back as Markdown, mirroring the write path.
        self.assertEqual(result["note"], "Chase the client")
        self.assertEqual(result["state"], "planned")

    def test_denied_ir_model_read_raises_the_pinned_message(self):
        client, _ = self._client(deny_ir_model=True)
        with self.assertRaises(ValueError) as ctx:
            schedule_activity(client, 42)
        self.assertEqual(str(ctx.exception), MODEL_LOOKUP_DENIED_MESSAGE)

    def test_unknown_model_name_is_reported(self):
        client, _ = self._client(model_rows=[])
        with self.assertRaises(ValueError) as ctx:
            schedule_activity(client, 42, res_model="not.a.model")
        self.assertIn("not.a.model", str(ctx.exception))

    def test_no_create_is_issued_when_the_type_cannot_be_resolved(self):
        client, executor = _client(type_rows=[[], [], []])
        with self.assertRaises(ValueError):
            schedule_activity(client, 42, activity_type="Nope")
        self.assertEqual(_calls_to(executor, "mail.activity", "create"), [])


class TestGetActivities(unittest.TestCase):
    """The read counterpart filters by record and/or user, deadline-first."""

    def _domain(self, executor: _RecordingExecutor):
        return _calls_to(executor, "mail.activity", "search_read")[0][2][0]

    def test_res_id_without_model_defaults_to_project_task(self):
        client, executor = _client()
        get_activities(client, res_id=42)
        self.assertEqual(
            self._domain(executor),
            [("res_model", "=", "project.task"), ("res_id", "=", 42)],
        )

    def test_explicit_model_is_honoured(self):
        client, executor = _client()
        get_activities(client, res_id=8, res_model="crm.lead")
        self.assertEqual(
            self._domain(executor),
            [("res_model", "=", "crm.lead"), ("res_id", "=", 8)],
        )

    def test_unfiltered_call_scopes_to_the_authenticated_user(self):
        # An unfiltered read would return every open activity in the database.
        client, executor = _client()
        get_activities(client)
        self.assertEqual(self._domain(executor), [("user_id", "=", 7)])

    def test_explicit_user_id_is_not_overridden(self):
        client, executor = _client()
        get_activities(client, user_id=12)
        self.assertEqual(self._domain(executor), [("user_id", "=", 12)])

    def test_record_filter_does_not_add_a_user_filter(self):
        # Asking for a record's activities must show everyone's, not just mine.
        client, executor = _client()
        get_activities(client, res_id=42)
        self.assertNotIn(("user_id", "=", 7), self._domain(executor))

    def test_fields_order_and_limit(self):
        client, executor = _client()
        get_activities(client, res_id=42, limit=5)
        _, _, _, kwargs = _calls_to(executor, "mail.activity", "search_read")[0]
        self.assertEqual(kwargs["fields"], _ACTIVITY_FIELDS)
        self.assertEqual(kwargs["order"], "date_deadline asc, id asc")
        self.assertEqual(kwargs["limit"], 5)

    def test_rows_are_shaped_with_ids_and_display_names(self):
        client, _ = _client()
        entry = get_activities(client, res_id=42)[0]
        self.assertEqual(entry["activity_id"], 1)
        self.assertEqual(entry["res_model"], "project.task")
        self.assertEqual(entry["res_id"], 42)
        self.assertEqual(entry["res_name"], "Fix VAT")
        self.assertEqual(entry["activity_type_id"], 4)
        self.assertEqual(entry["activity_type"], "To Do")
        self.assertEqual(entry["user_id"], 7)
        self.assertEqual(entry["user"], "Chris P")
        self.assertEqual(entry["note"], "Chase the client")
        self.assertEqual(entry["date_deadline"], "2026-09-10")

    def test_empty_many2ones_and_note_degrade_cleanly(self):
        client, _ = _client(
            activities=[
                _activity(type_pair=False, note="", user_id=False, summary=False)
            ]
        )
        entry = get_activities(client, res_id=42)[0]
        self.assertIsNone(entry["activity_type_id"])
        self.assertEqual(entry["activity_type"], "")
        self.assertIsNone(entry["user_id"])
        self.assertEqual(entry["user"], "")
        self.assertEqual(entry["summary"], "")
        self.assertEqual(entry["note"], "")

    def test_read_is_side_effect_free(self):
        client, executor = _client()
        get_activities(client, res_id=42)
        methods = {method for _, method, _, _ in executor.calls}
        self.assertTrue(methods <= {"search_read", "read"})


class TestMarkActivityDone(unittest.TestCase):
    """Completion reads first, then hands the feedback to ``action_feedback``."""

    def test_reads_the_activity_before_completing_it(self):
        # action_feedback DELETES the row, so the read must come first or the
        # returned description would be unrecoverable.
        client, executor = _client()
        mark_activity_done(client, 1, "done and dusted")
        sequence = [call[:2] for call in executor.calls]
        self.assertEqual(
            sequence,
            [("mail.activity", "read"), ("mail.activity", "action_feedback")],
        )

    def test_feedback_is_forwarded_as_a_keyword(self):
        client, executor = _client()
        mark_activity_done(client, 1, "spoke to client")
        _, _, args, kwargs = _calls_to(executor, "mail.activity", "action_feedback")[0]
        self.assertEqual(args[0], [1])
        self.assertEqual(kwargs, {"feedback": "spoke to client"})

    def test_result_describes_what_was_closed(self):
        client, _ = _client()
        result = mark_activity_done(client, 1, "spoke to client")
        self.assertTrue(result["done"])
        self.assertEqual(result["feedback"], "spoke to client")
        self.assertEqual(result["message_id"], 900)
        self.assertEqual(result["activity_id"], 1)
        self.assertEqual(result["activity_type"], "To Do")
        self.assertEqual(result["res_id"], 42)

    def test_falsy_feedback_result_becomes_a_null_message_id(self):
        client, _ = _client(feedback_result=False)
        self.assertIsNone(mark_activity_done(client, 1)["message_id"])

    def test_missing_activity_raises_before_any_feedback_call(self):
        client, executor = _client(activities=[])
        with self.assertRaises(ValueError) as ctx:
            mark_activity_done(client, 99)
        self.assertIn("mail.activity 99 not found", str(ctx.exception))
        # The message names the "already done" case, which is the likely cause.
        self.assertIn("marked done", str(ctx.exception))
        self.assertEqual(_calls_to(executor, "mail.activity", "action_feedback"), [])


class TestActivityCommands(unittest.TestCase):
    """Each built-in command registers and delegates to its helper."""

    def test_all_four_commands_are_registered(self):
        for name, cls in (
            ("schedule_activity", ScheduleActivityCommand),
            ("get_activities", GetActivitiesCommand),
            ("mark_activity_done", MarkActivityDoneCommand),
            ("search_activity_types", SearchActivityTypesCommand),
        ):
            self.assertIn(name, BUILTIN_COMMANDS)
            self.assertIs(BUILTIN_COMMANDS[name], cls)

    def test_every_description_is_non_empty(self):
        for cls in (
            ScheduleActivityCommand,
            GetActivitiesCommand,
            MarkActivityDoneCommand,
            SearchActivityTypesCommand,
        ):
            self.assertTrue(cls._description.strip())

    def test_schedule_delegates_every_argument(self):
        client = MagicMock()
        target = "odoo_sdk.commands.builtin.schedule_activity.schedule_activity"
        with patch(target, return_value={"activity_id": 1}) as helper:
            result = ScheduleActivityCommand(client).execute(
                42,
                res_model="crm.lead",
                activity_type="Call",
                summary="s",
                note="n",
                date_deadline="2026-09-10",
                user_id=12,
            )
        self.assertEqual(result, {"activity_id": 1})
        helper.assert_called_once_with(
            client,
            42,
            res_model="crm.lead",
            activity_type="Call",
            summary="s",
            note="n",
            date_deadline="2026-09-10",
            user_id=12,
        )

    def test_get_activities_delegates_every_argument(self):
        client = MagicMock()
        target = "odoo_sdk.commands.builtin.get_activities.get_activities"
        with patch(target, return_value=[]) as helper:
            GetActivitiesCommand(client).execute(
                res_id=42, res_model="crm.lead", user_id=12, limit=5
            )
        helper.assert_called_once_with(
            client, res_id=42, res_model="crm.lead", user_id=12, limit=5
        )

    def test_mark_done_delegates_every_argument(self):
        client = MagicMock()
        target = "odoo_sdk.commands.builtin.mark_activity_done.mark_activity_done"
        with patch(target, return_value={"done": True}) as helper:
            MarkActivityDoneCommand(client).execute(1, "fb")
        helper.assert_called_once_with(client, 1, "fb")

    def test_search_types_delegates_every_argument(self):
        client = MagicMock()
        target = (
            "odoo_sdk.commands.builtin.search_activity_types.search_activity_types"
        )
        with patch(target, return_value=[]) as helper:
            SearchActivityTypesCommand(client).execute(
                query="call", res_model="project.task", limit=5
            )
        helper.assert_called_once_with(
            client, query="call", res_model="project.task", limit=5
        )


class TestActivityToolExposure(unittest.TestCase):
    """The tools reach the MCP surface, not just the command registry (#677).

    Registering a command is NOT enough: the MCP layer owns ``_explicit_tools``,
    so a tool with no factory in ``ATOMIC_TOOL_FACTORIES`` is built by nothing
    and exposed by nothing. These assertions walk the whole path — factory to
    ``build_explicit_tools`` to the default (ungated) surface to the tools the
    server actually adds.
    """

    _TOOL_NAMES = (
        "schedule_activity",
        "get_activities",
        "mark_activity_done",
        "search_activity_types",
    )

    def _registry(self):
        from odoo_sdk.commands import Registry
        from odoo_sdk.commands.builtin import register_builtins

        return register_builtins(Registry(MagicMock()))

    def test_factories_are_registered_as_atomic_tools(self):
        from odoo_sdk.mcp.tools.atomic import ATOMIC_TOOL_FACTORIES

        for name in self._TOOL_NAMES:
            self.assertIn(name, ATOMIC_TOOL_FACTORIES)

    def test_tools_are_built_with_their_command_descriptions(self):
        from odoo_sdk.mcp.tools import build_explicit_tools

        tools = build_explicit_tools(self._registry())
        for name in self._TOOL_NAMES:
            self.assertIn(name, tools)
            _, description = tools[name]
            self.assertNotEqual(description, "")

    def test_tools_stay_on_the_default_ungated_surface(self):
        from odoo_sdk.mcp.tools import build_explicit_tools, default_tool_surface

        default = default_tool_surface(
            build_explicit_tools(self._registry()), include_gated=False
        )
        for name in self._TOOL_NAMES:
            self.assertIn(name, default)

    def test_server_registers_the_tools(self):
        from odoo_sdk.mcp.server import OdooMCPServer
        from odoo_sdk.mcp.tools import build_explicit_tools

        registry = self._registry()
        added: list[Any] = []
        mock_mcp = MagicMock()
        mock_mcp.add_tool.side_effect = added.append
        with patch("odoo_sdk.mcp.server.FastMCP", return_value=mock_mcp):
            OdooMCPServer(registry, explicit_tools=build_explicit_tools(registry))
        exposed = {tool.name for tool in added}
        for name in self._TOOL_NAMES:
            self.assertIn(name, exposed)


class TestActivityToolSchemas(unittest.TestCase):
    """The wire schemas keep the convenience defaults reachable."""

    def _schema(self, factory, name: str):
        from fastmcp.tools import Tool

        class _Reg:
            def __getitem__(self, key):
                cmd = MagicMock()
                cmd.execute.side_effect = lambda *a, **k: {"args": a, "kwargs": k}
                return cmd

        return Tool.from_function(factory(_Reg()), name=name).parameters

    def test_schedule_requires_only_res_id(self):
        from odoo_sdk.mcp.tools.atomic import make_schedule_activity_tool

        schema = self._schema(make_schedule_activity_tool, "schedule_activity")
        self.assertEqual(schema["required"], ["res_id"])
        self.assertEqual(
            schema["properties"]["res_model"].get("default"), "project.task"
        )
        for name in ("activity_type", "summary", "note", "date_deadline", "user_id"):
            self.assertIn(name, schema["properties"])

    def test_get_activities_requires_nothing(self):
        from odoo_sdk.mcp.tools.atomic import make_get_activities_tool

        schema = self._schema(make_get_activities_tool, "get_activities")
        self.assertEqual(schema.get("required", []), [])
        self.assertEqual(schema["properties"]["limit"].get("default"), 50)

    def test_mark_done_requires_only_the_activity_id(self):
        from odoo_sdk.mcp.tools.atomic import make_mark_activity_done_tool

        schema = self._schema(make_mark_activity_done_tool, "mark_activity_done")
        self.assertEqual(schema["required"], ["activity_id"])
        self.assertEqual(schema["properties"]["feedback"].get("default"), "")

    def test_search_types_requires_nothing(self):
        from odoo_sdk.mcp.tools.atomic import make_search_activity_types_tool

        schema = self._schema(
            make_search_activity_types_tool, "search_activity_types"
        )
        self.assertEqual(schema.get("required", []), [])

    def test_schedule_tool_forwards_every_argument_to_the_command(self):
        from odoo_sdk.mcp.tools.atomic import make_schedule_activity_tool

        class _Reg:
            def __getitem__(self, key):
                cmd = MagicMock()
                cmd.execute.side_effect = lambda *a, **k: {"args": a, "kwargs": k}
                return cmd

        result = make_schedule_activity_tool(_Reg())(42, activity_type="Call")
        self.assertEqual(result["args"], (42,))
        self.assertEqual(
            result["kwargs"],
            {
                "res_model": "project.task",
                "activity_type": "Call",
                "summary": "",
                "note": "",
                "date_deadline": None,
                "user_id": None,
            },
        )


class TestActivityToonEncoding(unittest.TestCase):
    """The structured results encode cleanly under the TOON output flag."""

    def test_activity_list_toon_encodes(self):
        from odoo_sdk.mcp.server import TOON_OUTPUT_ENV, _to_toon

        client, _ = _client()
        result = get_activities(client, res_id=42)
        with patch.dict("os.environ", {TOON_OUTPUT_ENV: "1"}):
            out = _to_toon(result)
        self.assertIsInstance(out, str)
        self.assertIn("To Do", out)


if __name__ == "__main__":
    unittest.main()
