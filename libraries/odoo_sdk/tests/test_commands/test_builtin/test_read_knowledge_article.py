"""Tests for the read-only ``read_knowledge_article`` MCP tool (issue #249).

The helper is driven through a real :class:`OdooClient` wrapping a recording
fake executor so the exact ``knowledge.article`` ``read`` id / fields issued to
Odoo are asserted, and the full HTML-to-Markdown body conversion (plus the
generous body cap) is exercised end-to-end. ``knowledge.article`` is an Odoo
Enterprise model whose availability is determined by attempting the real query —
there is *no* ``ir.model`` probe (issue #444), so every test asserts ``ir.model``
is never touched. The model-absent (Community) path is pinned to its exact
``ValueError`` message, a permission denial is pinned to propagating as the
original :class:`OdooAccessError`, and a missing id is pinned to the exact
id-naming ``ValueError``. No live Odoo is used.
"""

import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from odoo_sdk.client import OdooClient
from odoo_sdk.commands.builtin import BUILTIN_COMMANDS
from odoo_sdk.commands.builtin.read_knowledge_article import (
    ReadKnowledgeArticleCommand,
)
from odoo_sdk.transport.errors import (
    OdooAccessError,
    OdooMissingRecordError,
    OdooServerError,
)
from odoo_sdk.transport.executor import OdooExecutor
from odoo_sdk.utilities.knowledge import (
    BODY_CHAR_CAP,
    KNOWLEDGE_UNAVAILABLE_MESSAGE,
    _is_missing_model_error,
    read_knowledge_article,
)

_READ_FIELDS = ["id", "name", "body", "write_date"]

# A realistic missing-model fault: Odoo resolves ``env["knowledge.article"]`` to a
# ``KeyError`` that surfaces across transports as an unmapped ``OdooServerError``.
_MODEL_ABSENT_ERROR = OdooServerError(
    "KeyError: 'knowledge.article'",
    model="knowledge.article",
    fault_string="Traceback ...\nKeyError: 'knowledge.article'",
)
# A realistic permission denial for a least-privileged (non-admin) account.
_ACCESS_ERROR = OdooAccessError(
    "You are not allowed to access 'Article' (knowledge.article) records.",
    model="knowledge.article",
    method="read",
)


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
    """Fake executor recording every call; returns rows or raises a canned fault.

    Real ``OdooClient`` execution runs through this (including the system-wide
    ``forbid_unlink`` guard), and every issued call is captured in ``calls`` so
    the exact ``read`` id / fields can be asserted and — as the #444 regression
    guard — so tests can prove ``ir.model`` is never touched. When ``error`` is
    set the call raises it (modelling a Community/missing-model fault or a
    permission denial); otherwise the canned rows are returned (an empty list
    models a missing/inaccessible id).
    """

    def __init__(
        self, rows: list[dict] | None = None, *, error: Exception | None = None
    ) -> None:
        self._rows = rows if rows is not None else []
        self._error = error
        self.calls: list[tuple[str, str, tuple[Any, ...], dict[str, Any]]] = []

    def execute(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((model, method, args, kwargs))
        if self._error is not None:
            raise self._error
        return self._rows


def _client(
    rows: list[dict] | None = None, *, error: Exception | None = None
) -> tuple[OdooClient, _RecordingExecutor]:
    executor = _RecordingExecutor(rows, error=error)
    return OdooClient(executor=executor), executor


def _assert_never_probes_ir_model(executor: _RecordingExecutor) -> None:
    """Fail if any recorded call touched the administrative ``ir.model`` table."""
    models = [model for model, _, _, _ in executor.calls]
    assert "ir.model" not in models, f"ir.model must never be probed: {executor.calls}"


class TestReadKnowledgeArticleQuery(unittest.TestCase):
    """The tool issues a single ``read`` of the requested id and fields."""

    def test_reads_the_requested_id_without_probing_ir_model(self):
        client, executor = _client([_article(id=7)])
        read_knowledge_article(client, 7)
        # A single read-only call: the article read. No ir.model probe.
        self.assertEqual(len(executor.calls), 1)
        _assert_never_probes_ir_model(executor)
        model, method, args, _ = executor.calls[0]
        self.assertEqual((model, method), ("knowledge.article", "read"))
        self.assertEqual(args[0], [7])

    def test_reads_expected_fields(self):
        client, executor = _client([_article()])
        read_knowledge_article(client, 7)
        self.assertEqual(executor.calls[0][3]["fields"], _READ_FIELDS)

    def test_only_read_only_methods_issued(self):
        client, executor = _client([_article()])
        read_knowledge_article(client, 7)
        methods = [method for _, method, _, _ in executor.calls]
        self.assertEqual(methods, ["read"])
        _assert_never_probes_ir_model(executor)

    def test_missing_id_raises_exact_message_naming_the_id(self):
        client, executor = _client([])  # empty read result => id not found
        with self.assertRaises(ValueError) as ctx:
            read_knowledge_article(client, 404)
        self.assertEqual(str(ctx.exception), "knowledge.article 404 not found")
        # The single read was attempted before raising.
        self.assertEqual(len(executor.calls), 1)
        _assert_never_probes_ir_model(executor)


class TestReadKnowledgeArticleAvailability(unittest.TestCase):
    """Availability comes from the real query's fault, never an ir.model probe."""

    def test_absent_model_raises_exact_community_message(self):
        client, executor = _client(error=_MODEL_ABSENT_ERROR)
        with self.assertRaises(ValueError) as ctx:
            read_knowledge_article(client, 7)
        self.assertEqual(str(ctx.exception), KNOWLEDGE_UNAVAILABLE_MESSAGE)
        self.assertEqual(
            KNOWLEDGE_UNAVAILABLE_MESSAGE,
            "knowledge.article model not available (Odoo Enterprise required)",
        )
        # The real query was attempted directly; ir.model was never probed.
        self.assertEqual(len(executor.calls), 1)
        self.assertEqual(executor.calls[0][:2], ("knowledge.article", "read"))
        _assert_never_probes_ir_model(executor)

    def test_permission_error_propagates_and_is_not_relabeled(self):
        client, executor = _client(error=_ACCESS_ERROR)
        # A least-privileged account's access denial must surface as-is, NOT be
        # swallowed into the edition ValueError.
        with self.assertRaises(OdooAccessError) as ctx:
            read_knowledge_article(client, 7)
        self.assertIs(ctx.exception, _ACCESS_ERROR)
        self.assertNotEqual(str(ctx.exception), KNOWLEDGE_UNAVAILABLE_MESSAGE)
        _assert_never_probes_ir_model(executor)

    def test_missing_record_error_propagates_not_relabeled(self):
        # A MissingError names the model in its text, but it is a missing *record*
        # (bad id), not a missing model — it must not become the edition error.
        missing = OdooMissingRecordError(
            "Record does not exist or has been deleted. "
            "(Records: knowledge.article(404,), User: 2)",
            model="knowledge.article",
        )
        client, _ = _client(error=missing)
        with self.assertRaises(OdooMissingRecordError) as ctx:
            read_knowledge_article(client, 404)
        self.assertIs(ctx.exception, missing)

    def test_edition_and_permission_errors_are_distinguishable(self):
        absent_client, _ = _client(error=_MODEL_ABSENT_ERROR)
        access_client, _ = _client(error=_ACCESS_ERROR)
        with self.assertRaises(ValueError) as absent_ctx:
            read_knowledge_article(absent_client, 7)
        with self.assertRaises(OdooAccessError) as access_ctx:
            read_knowledge_article(access_client, 7)
        self.assertNotIsInstance(access_ctx.exception, ValueError)
        self.assertIsInstance(absent_ctx.exception, ValueError)


class TestIsMissingModelError(unittest.TestCase):
    """The classifier separates a missing model from every other fault."""

    def test_unmapped_keyerror_fault_is_missing_model(self):
        self.assertTrue(_is_missing_model_error(_MODEL_ABSENT_ERROR))

    def test_marker_in_fault_string_only_is_detected(self):
        exc = OdooServerError(
            "server error", fault_string="odoo model not found for knowledge.article"
        )
        self.assertTrue(_is_missing_model_error(exc))

    def test_access_error_is_not_missing_model(self):
        self.assertFalse(_is_missing_model_error(_ACCESS_ERROR))

    def test_missing_record_error_is_not_missing_model(self):
        exc = OdooMissingRecordError("Record does not exist or has been deleted.")
        self.assertFalse(_is_missing_model_error(exc))

    def test_generic_server_error_is_not_missing_model(self):
        self.assertFalse(_is_missing_model_error(OdooServerError("boom")))

    def test_marker_without_model_name_is_not_missing_model(self):
        # An unrelated business UserError (mapped to OdooServerError) can
        # legitimately say "does not exist" about something else entirely (a
        # broken cross-reference, a missing linked document); it must not be
        # misread as the knowledge.article model itself being absent, which
        # would hide the real failure behind the Community-edition message.
        exc = OdooServerError("The linked document does not exist anymore.")
        self.assertFalse(_is_missing_model_error(exc))

    def test_model_name_without_marker_is_not_missing_model(self):
        exc = OdooServerError("knowledge.article write failed unexpectedly")
        self.assertFalse(_is_missing_model_error(exc))


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
        # A single read-only read, no ir.model probe and no writes.
        methods = [method for _, method, _, _ in executor.calls]
        self.assertEqual(methods, ["read"])
        _assert_never_probes_ir_model(executor)


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
