"""Tests for the read-only ``search_chatter`` MCP tool (issue #246).

The helper is driven through a real :class:`OdooClient` wrapping a recording
fake executor so the exact ``search_read`` domain / fields / order / limit issued
to Odoo are asserted, and the shared chatter shaping (display-name extraction +
HTML-to-Markdown body) is exercised end-to-end. No live Odoo is used.
"""

import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from odoo_sdk.client import OdooClient
from odoo_sdk.commands.builtin import BUILTIN_COMMANDS
from odoo_sdk.commands.builtin.search_chatter import SearchChatterCommand
from odoo_sdk.transport.executor import OdooExecutor
from odoo_sdk.utilities.odoo_helpers import search_chatter, shape_chatter_message

_SEARCH_FIELDS = [
    "id",
    "date",
    "author_id",
    "message_type",
    "subtype_id",
    "body",
    "model",
    "res_id",
]


def _msg(**overrides: Any) -> dict:
    """Build a raw ``mail.message`` row with sensible defaults."""
    row = {
        "id": 1,
        "date": "2026-06-20T10:30:00",
        "author_id": [5, "Jane Smith"],
        "message_type": "comment",
        "subtype_id": [1, "Discussions"],
        "body": "<p>Hello world</p>",
        "model": "project.task",
        "res_id": 42,
    }
    row.update(overrides)
    return row


class _RecordingExecutor(OdooExecutor):
    """Fake executor recording every call and returning canned rows.

    Real ``OdooClient`` execution runs through this (including the system-wide
    ``forbid_unlink`` guard), and every issued call is captured in ``calls`` so
    the exact domain / fields / order / limit can be asserted.
    """

    def __init__(self, rows: list[dict] | None = None) -> None:
        self._rows = rows if rows is not None else []
        self.calls: list[tuple[str, str, tuple[Any, ...], dict[str, Any]]] = []

    def execute(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((model, method, args, kwargs))
        return self._rows


def _client(rows: list[dict] | None = None) -> tuple[OdooClient, _RecordingExecutor]:
    executor = _RecordingExecutor(rows)
    return OdooClient(executor=executor), executor


class TestSearchChatterDomain(unittest.TestCase):
    """The single ``search_read`` domain reflects exactly the supplied filters."""

    def _domain(self, executor: _RecordingExecutor) -> list:
        model, method, args, _ = executor.calls[0]
        self.assertEqual((model, method), ("mail.message", "search_read"))
        return args[0]

    def test_query_only_issues_body_ilike(self):
        client, executor = _client()
        search_chatter(client, "hello")
        self.assertEqual(len(executor.calls), 1)
        self.assertEqual(self._domain(executor), [("body", "ilike", "hello")])

    def test_fields_order_and_default_limit(self):
        client, executor = _client()
        search_chatter(client, "hello")
        _, _, _, kwargs = executor.calls[0]
        self.assertEqual(kwargs["fields"], _SEARCH_FIELDS)
        self.assertEqual(kwargs["order"], "date desc")
        self.assertEqual(kwargs["limit"], 20)

    def test_custom_limit_forwarded(self):
        client, executor = _client()
        search_chatter(client, "hello", limit=5)
        self.assertEqual(executor.calls[0][3]["limit"], 5)

    def test_model_filter_appended(self):
        client, executor = _client()
        search_chatter(client, "hello", model="project.task")
        self.assertEqual(
            self._domain(executor),
            [("body", "ilike", "hello"), ("model", "=", "project.task")],
        )

    def test_record_id_filter_appended(self):
        client, executor = _client()
        search_chatter(client, "hello", record_id=42)
        self.assertEqual(
            self._domain(executor),
            [("body", "ilike", "hello"), ("res_id", "=", 42)],
        )

    def test_date_bounds_appended(self):
        client, executor = _client()
        search_chatter(client, "hello", date_from="2026-01-01", date_to="2026-02-01")
        self.assertEqual(
            self._domain(executor),
            [
                ("body", "ilike", "hello"),
                ("date", ">=", "2026-01-01"),
                ("date", "<=", "2026-02-01"),
            ],
        )

    def test_all_filters_combined_in_order(self):
        client, executor = _client()
        search_chatter(
            client,
            "hello",
            model="project.task",
            record_id=42,
            date_from="2026-01-01",
            date_to="2026-02-01",
        )
        self.assertEqual(
            self._domain(executor),
            [
                ("body", "ilike", "hello"),
                ("model", "=", "project.task"),
                ("res_id", "=", 42),
                ("date", ">=", "2026-01-01"),
                ("date", "<=", "2026-02-01"),
            ],
        )


class TestSearchChatterShaping(unittest.TestCase):
    """Results reuse the shared chatter shaping and add navigation fields."""

    def test_shapes_and_adds_res_model_res_id(self):
        client, _ = _client([_msg()])
        result = search_chatter(client, "hello")
        self.assertEqual(len(result), 1)
        entry = result[0]
        self.assertEqual(entry["author"], "Jane Smith")
        self.assertEqual(entry["subtype"], "Discussions")
        self.assertEqual(entry["type"], "comment")
        self.assertEqual(entry["res_model"], "project.task")
        self.assertEqual(entry["res_id"], 42)

    def test_body_stripped_to_markdown(self):
        client, _ = _client([_msg(body="<p>Hello <b>world</b></p>")])
        entry = search_chatter(client, "hello")[0]
        self.assertNotIn("<p>", entry["body"])
        self.assertNotIn("<b>", entry["body"])
        self.assertIn("Hello", entry["body"])

    def test_empty_body_yields_empty_string(self):
        client, _ = _client([_msg(body="")])
        self.assertEqual(search_chatter(client, "hello")[0]["body"], "")

    def test_no_matches_returns_empty_list(self):
        client, executor = _client([])
        self.assertEqual(search_chatter(client, "nope"), [])
        self.assertEqual(len(executor.calls), 1)


class TestShapeChatterMessage(unittest.TestCase):
    """The shared shaping helper is the single presentation point."""

    def test_extracts_display_names_and_converts_body(self):
        shaped = shape_chatter_message(_msg(body="<p>Hi</p>"))
        self.assertEqual(shaped["author"], "Jane Smith")
        self.assertEqual(shaped["subtype"], "Discussions")
        self.assertIn("Hi", shaped["body"])
        self.assertNotIn("<p>", shaped["body"])
        # Base shaping carries no navigation fields; those are added by callers.
        self.assertNotIn("res_model", shaped)

    def test_missing_author_and_subtype_default_to_empty(self):
        shaped = shape_chatter_message(_msg(author_id=False, subtype_id=False))
        self.assertEqual(shaped["author"], "")
        self.assertEqual(shaped["subtype"], "")


class TestSearchChatterCommand(unittest.TestCase):
    """The built-in command registers and delegates to the helper."""

    def test_registered_under_name(self):
        self.assertIn("search_chatter", BUILTIN_COMMANDS)
        self.assertIs(BUILTIN_COMMANDS["search_chatter"], SearchChatterCommand)

    def test_execute_delegates_all_kwargs(self):
        client = MagicMock()
        target = "odoo_sdk.commands.builtin.search_chatter.search_chatter"
        with patch(target, return_value=["shaped"]) as helper:
            result = SearchChatterCommand(client).execute(
                "hello",
                model="project.task",
                record_id=42,
                date_from="2026-01-01",
                date_to="2026-02-01",
                limit=7,
            )
        self.assertEqual(result, ["shaped"])
        helper.assert_called_once_with(
            client,
            "hello",
            model="project.task",
            record_id=42,
            date_from="2026-01-01",
            date_to="2026-02-01",
            limit=7,
        )

    def test_execute_defaults_are_read_only_search(self):
        client, executor = _client([_msg()])
        SearchChatterCommand(client).execute("hello")
        # A single read-only search_read call, no writes of any kind.
        self.assertEqual(len(executor.calls), 1)
        model, method, _, _ = executor.calls[0]
        self.assertEqual((model, method), ("mail.message", "search_read"))


class TestSearchChatterToonEncoding(unittest.TestCase):
    """The list-of-dicts result encodes cleanly under the TOON output flag."""

    def test_result_toon_encodes(self):
        from odoo_sdk.mcp.server import TOON_OUTPUT_ENV, _to_toon

        client, _ = _client([_msg(id=9, body="<p>needle</p>")])
        result = search_chatter(client, "needle")
        with patch.dict("os.environ", {TOON_OUTPUT_ENV: "1"}):
            out = _to_toon(result)
        self.assertIsInstance(out, str)
        self.assertIn("project.task", out)
        self.assertIn("needle", out)


if __name__ == "__main__":
    unittest.main()
