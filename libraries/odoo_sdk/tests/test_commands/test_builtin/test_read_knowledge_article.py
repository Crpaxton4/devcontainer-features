"""Tests for the read-only ``read_knowledge_article`` MCP tool (issue #249).

The helper is driven through a real :class:`OdooClient` wrapping a recording
fake executor so the exact ``ir.model`` capability probe and the
``knowledge.article`` ``read`` id / fields issued to Odoo are asserted, and the
full HTML-to-Markdown body conversion (plus the generous body cap) is exercised
end-to-end. ``knowledge.article`` is an Odoo Enterprise model, so the
model-absent (Community) path is pinned to its exact ``ValueError`` message, and
a missing id is pinned to the exact id-naming ``ValueError``. No live Odoo is
used.
"""

import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from odoo_sdk.client import OdooClient
from odoo_sdk.commands.builtin import BUILTIN_COMMANDS
from odoo_sdk.commands.builtin.read_knowledge_article import (
    ReadKnowledgeArticleCommand,
)
from odoo_sdk.transport.executor import OdooExecutor
from odoo_sdk.utilities.knowledge import (
    BODY_CHAR_CAP,
    KNOWLEDGE_UNAVAILABLE_MESSAGE,
    _article_not_found_message,
    assert_knowledge_available,
    read_knowledge_article,
)

_READ_FIELDS = ["id", "name", "body", "write_date"]
_PROBE_DOMAIN = [("model", "=", "knowledge.article")]


def _article(**overrides: Any) -> dict:
    """Build a raw ``knowledge.article`` row with sensible defaults."""
    row = {
        "id": 7,
        "name": "VAT rounding guide",
        "body": "<p>How to round <b>VAT</b>.</p>",
        "write_date": "2026-06-20 10:30:00",
    }
    row.update(overrides)
    return row


class _RecordingExecutor(OdooExecutor):
    """Fake executor recording every call; probes present/absent, returns rows.

    Real ``OdooClient`` execution runs through this (including the system-wide
    ``forbid_unlink`` guard), and every issued call is captured in ``calls`` so
    the exact probe domain and the ``read`` id / fields can be asserted.
    ``ir.model.search_count`` reports the model's presence and
    ``knowledge.article.read`` returns the canned rows (an empty list models a
    missing/inaccessible id).
    """

    def __init__(
        self, rows: list[dict] | None = None, *, model_present: bool = True
    ) -> None:
        self._rows = rows if rows is not None else []
        self._present = model_present
        self.calls: list[tuple[str, str, tuple[Any, ...], dict[str, Any]]] = []

    def execute(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((model, method, args, kwargs))
        if (model, method) == ("ir.model", "search_count"):
            return 1 if self._present else 0
        return self._rows


def _client(
    rows: list[dict] | None = None, *, model_present: bool = True
) -> tuple[OdooClient, _RecordingExecutor]:
    executor = _RecordingExecutor(rows, model_present=model_present)
    return OdooClient(executor=executor), executor


class TestReadKnowledgeArticleQuery(unittest.TestCase):
    """The probe precedes a single ``read`` of the requested id and fields."""

    def test_probes_then_reads_the_requested_id(self):
        client, executor = _client([_article(id=7)])
        read_knowledge_article(client, 7)
        # Two read-only calls: the ir.model probe, then the article read.
        self.assertEqual(len(executor.calls), 2)
        self.assertEqual(executor.calls[0][:2], ("ir.model", "search_count"))
        self.assertEqual(executor.calls[0][2][0], _PROBE_DOMAIN)
        model, method, args, _ = executor.calls[1]
        self.assertEqual((model, method), ("knowledge.article", "read"))
        self.assertEqual(args[0], [7])

    def test_reads_expected_fields(self):
        client, executor = _client([_article()])
        read_knowledge_article(client, 7)
        self.assertEqual(executor.calls[1][3]["fields"], _READ_FIELDS)

    def test_only_read_only_methods_issued(self):
        client, executor = _client([_article()])
        read_knowledge_article(client, 7)
        methods = [method for _, method, _, _ in executor.calls]
        self.assertEqual(methods, ["search_count", "read"])

    def test_absent_model_raises_exact_message_and_skips_read(self):
        client, executor = _client([_article()], model_present=False)
        with self.assertRaises(ValueError) as ctx:
            read_knowledge_article(client, 7)
        self.assertEqual(str(ctx.exception), KNOWLEDGE_UNAVAILABLE_MESSAGE)
        self.assertEqual(
            KNOWLEDGE_UNAVAILABLE_MESSAGE,
            "knowledge.article model not available (Odoo Enterprise required)",
        )
        # Only the probe ran; no knowledge.article read was issued.
        self.assertEqual(len(executor.calls), 1)
        self.assertEqual(executor.calls[0][:2], ("ir.model", "search_count"))

    def test_missing_id_raises_exact_message_naming_the_id(self):
        client, executor = _client([])  # empty read result => id not found
        with self.assertRaises(ValueError) as ctx:
            read_knowledge_article(client, 404)
        self.assertEqual(str(ctx.exception), "knowledge.article 404 not found")
        self.assertEqual(
            str(ctx.exception), _article_not_found_message(404)
        )
        # The probe passed and the read was attempted before raising.
        self.assertEqual(len(executor.calls), 2)


class TestReadKnowledgeArticleShaping(unittest.TestCase):
    """Results carry id/name/body/write_date/truncated with Markdown body."""

    def test_shapes_expected_keys_and_values(self):
        client, _ = _client([_article(id=9)])
        result = read_knowledge_article(client, 9)
        self.assertEqual(
            set(result), {"id", "name", "body", "write_date", "truncated"}
        )
        self.assertEqual(result["id"], 9)
        self.assertEqual(result["name"], "VAT rounding guide")
        self.assertEqual(result["write_date"], "2026-06-20 10:30:00")
        self.assertFalse(result["truncated"])

    def test_body_converted_html_to_markdown(self):
        from odoo_sdk.utilities.html import html_to_markdown

        body_html = "<p>Hello <b>world</b></p>"
        client, _ = _client([_article(body=body_html)])
        result = read_knowledge_article(client, 7)
        self.assertNotIn("<p>", result["body"])
        self.assertNotIn("<b>", result["body"])
        self.assertIn("Hello", result["body"])
        # Full body conversion, identical to the pure converter's output.
        self.assertEqual(result["body"], html_to_markdown(body_html))

    def test_empty_body_yields_empty_string_not_truncated(self):
        client, _ = _client([_article(body="")])
        result = read_knowledge_article(client, 7)
        self.assertEqual(result["body"], "")
        self.assertFalse(result["truncated"])

    def test_none_body_yields_empty_string(self):
        client, _ = _client([_article(body=False)])
        result = read_knowledge_article(client, 7)
        self.assertEqual(result["body"], "")
        self.assertFalse(result["truncated"])


class TestReadKnowledgeArticleBodyCap(unittest.TestCase):
    """The full body is capped at :data:`BODY_CHAR_CAP` with a truncation flag."""

    def test_over_cap_body_truncated_with_ellipsis_and_flag(self):
        body = "<p>" + ("x" * (BODY_CHAR_CAP + 100)) + "</p>"
        client, _ = _client([_article(body=body)])
        result = read_knowledge_article(client, 7)
        self.assertTrue(result["truncated"])
        self.assertEqual(len(result["body"]), BODY_CHAR_CAP + 1)
        self.assertTrue(result["body"].endswith("…"))
        # Only the trailing ellipsis is added; the first cap chars are content.
        self.assertNotIn("…", result["body"][:-1])

    def test_at_cap_body_not_truncated(self):
        body = "<p>" + ("x" * BODY_CHAR_CAP) + "</p>"
        client, _ = _client([_article(body=body)])
        result = read_knowledge_article(client, 7)
        self.assertFalse(result["truncated"])
        self.assertEqual(len(result["body"]), BODY_CHAR_CAP)
        self.assertNotIn("…", result["body"])


class TestReadKnowledgeArticleCommand(unittest.TestCase):
    """The built-in command registers and delegates to the helper."""

    def test_registered_under_name(self):
        self.assertIn("read_knowledge_article", BUILTIN_COMMANDS)
        self.assertIs(
            BUILTIN_COMMANDS["read_knowledge_article"],
            ReadKnowledgeArticleCommand,
        )

    def test_execute_delegates_article_id(self):
        client = MagicMock()
        target = (
            "odoo_sdk.commands.builtin.read_knowledge_article."
            "read_knowledge_article"
        )
        with patch(target, return_value={"shaped": True}) as helper:
            result = ReadKnowledgeArticleCommand(client).execute(9)
        self.assertEqual(result, {"shaped": True})
        helper.assert_called_once_with(client, 9)

    def test_execute_defaults_are_read_only(self):
        client, executor = _client([_article()])
        ReadKnowledgeArticleCommand(client).execute(7)
        # A read-only probe + read, no writes of any kind.
        methods = [method for _, method, _, _ in executor.calls]
        self.assertEqual(methods, ["search_count", "read"])


class TestAssertKnowledgeAvailableShared(unittest.TestCase):
    """The shared Enterprise probe is reused, not re-implemented."""

    def test_probe_present_issues_single_search_count(self):
        client, executor = _client(model_present=True)
        assert_knowledge_available(client)
        self.assertEqual(len(executor.calls), 1)
        self.assertEqual(executor.calls[0][:2], ("ir.model", "search_count"))


class TestReadKnowledgeArticleToonEncoding(unittest.TestCase):
    """The single-dict result encodes cleanly under the TOON output flag."""

    def test_result_toon_encodes(self):
        from odoo_sdk.mcp.server import TOON_OUTPUT_ENV, _to_toon

        client, _ = _client([_article(id=9, body="<p>needle</p>")])
        result = read_knowledge_article(client, 9)
        with patch.dict("os.environ", {TOON_OUTPUT_ENV: "1"}):
            out = _to_toon(result)
        self.assertIsInstance(out, str)
        self.assertIn("VAT rounding guide", out)
        self.assertIn("needle", out)


if __name__ == "__main__":
    unittest.main()
