"""Tests for the read-only ``search_knowledge_articles`` MCP tool (issue #248).

The helper is driven through a real :class:`OdooClient` wrapping a recording
fake executor so the exact ``ir.model`` capability probe and the
``knowledge.article`` ``search_read`` domain / fields / order / limit issued to
Odoo are asserted, and the HTML-to-Markdown body snippet capping is exercised
end-to-end. ``knowledge.article`` is an Odoo Enterprise model, so the
model-absent (Community) path is pinned to its exact ``ValueError`` message. No
live Odoo is used.
"""

import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from odoo_sdk.client import OdooClient
from odoo_sdk.commands.builtin import BUILTIN_COMMANDS
from odoo_sdk.commands.builtin.search_knowledge_articles import (
    SearchKnowledgeArticlesCommand,
)
from odoo_sdk.transport.executor import OdooExecutor
from odoo_sdk.utilities.knowledge import (
    KNOWLEDGE_UNAVAILABLE_MESSAGE,
    SNIPPET_CHAR_CAP,
    _article_snippet,
    assert_knowledge_available,
    search_knowledge_articles,
)

_SEARCH_FIELDS = ["id", "name", "body", "write_date"]
_PROBE_DOMAIN = [("model", "=", "knowledge.article")]
_SEARCH_DOMAIN = ["|", ("name", "ilike", "vat"), ("body", "ilike", "vat")]


def _article(**overrides: Any) -> dict:
    """Build a raw ``knowledge.article`` row with sensible defaults."""
    row = {
        "id": 1,
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
    the exact probe domain and search domain / fields / order / limit can be
    asserted. ``ir.model.search_count`` reports the model's presence and
    ``knowledge.article.search_read`` returns the canned rows.
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


class TestAssertKnowledgeAvailable(unittest.TestCase):
    """The Enterprise-only probe gates every read with a clear typed error."""

    def test_probe_uses_ir_model_search_count(self):
        client, executor = _client(model_present=True)
        assert_knowledge_available(client)
        self.assertEqual(len(executor.calls), 1)
        model, method, args, _ = executor.calls[0]
        self.assertEqual((model, method), ("ir.model", "search_count"))
        self.assertEqual(args[0], _PROBE_DOMAIN)

    def test_absent_model_raises_exact_message(self):
        client, _ = _client(model_present=False)
        with self.assertRaises(ValueError) as ctx:
            assert_knowledge_available(client)
        self.assertEqual(str(ctx.exception), KNOWLEDGE_UNAVAILABLE_MESSAGE)
        self.assertEqual(
            KNOWLEDGE_UNAVAILABLE_MESSAGE,
            "knowledge.article model not available (Odoo Enterprise required)",
        )


class TestSearchKnowledgeArticlesQuery(unittest.TestCase):
    """The probe precedes a single ``search_read`` with the exact OR domain."""

    def test_probes_then_searches_with_or_domain(self):
        client, executor = _client([_article()])
        search_knowledge_articles(client, "vat")
        # Two read-only calls: the ir.model probe, then the article search.
        self.assertEqual(len(executor.calls), 2)
        self.assertEqual(executor.calls[0][:2], ("ir.model", "search_count"))
        model, method, args, _ = executor.calls[1]
        self.assertEqual((model, method), ("knowledge.article", "search_read"))
        self.assertEqual(args[0], _SEARCH_DOMAIN)

    def test_fields_order_and_default_limit(self):
        client, executor = _client([_article()])
        search_knowledge_articles(client, "vat")
        _, _, _, kwargs = executor.calls[1]
        self.assertEqual(kwargs["fields"], _SEARCH_FIELDS)
        self.assertEqual(kwargs["order"], "write_date desc, id desc")
        self.assertEqual(kwargs["limit"], 10)

    def test_custom_limit_forwarded(self):
        client, executor = _client([_article()])
        search_knowledge_articles(client, "vat", limit=3)
        self.assertEqual(executor.calls[1][3]["limit"], 3)

    def test_absent_model_skips_search(self):
        client, executor = _client([_article()], model_present=False)
        with self.assertRaises(ValueError) as ctx:
            search_knowledge_articles(client, "vat")
        self.assertEqual(str(ctx.exception), KNOWLEDGE_UNAVAILABLE_MESSAGE)
        # Only the probe ran; no knowledge.article search was issued.
        self.assertEqual(len(executor.calls), 1)
        self.assertEqual(executor.calls[0][:2], ("ir.model", "search_count"))


class TestSearchKnowledgeArticlesShaping(unittest.TestCase):
    """Results carry id/name/snippet/write_date with an HTML-stripped preview."""

    def test_shapes_expected_keys(self):
        client, _ = _client([_article(id=9)])
        result = search_knowledge_articles(client, "vat")
        self.assertEqual(len(result), 1)
        entry = result[0]
        self.assertEqual(set(entry), {"id", "name", "snippet", "write_date"})
        self.assertEqual(entry["id"], 9)
        self.assertEqual(entry["name"], "VAT rounding guide")
        self.assertEqual(entry["write_date"], "2026-06-20 10:30:00")

    def test_snippet_stripped_to_markdown(self):
        client, _ = _client([_article(body="<p>Hello <b>world</b></p>")])
        entry = search_knowledge_articles(client, "vat")[0]
        self.assertNotIn("<p>", entry["snippet"])
        self.assertNotIn("<b>", entry["snippet"])
        self.assertIn("Hello", entry["snippet"])

    def test_empty_body_yields_empty_snippet(self):
        client, _ = _client([_article(body="")])
        self.assertEqual(search_knowledge_articles(client, "vat")[0]["snippet"], "")

    def test_no_matches_returns_empty_list(self):
        client, executor = _client([])
        self.assertEqual(search_knowledge_articles(client, "nope"), [])
        # Probe + search both ran even though there were no matches.
        self.assertEqual(len(executor.calls), 2)


class TestArticleSnippet(unittest.TestCase):
    """The snippet helper caps long previews at :data:`SNIPPET_CHAR_CAP`."""

    def test_short_body_returned_verbatim(self):
        from odoo_sdk.utilities.html import html_to_markdown

        body = "<p>Hello <b>world</b></p>"
        self.assertEqual(_article_snippet(body), html_to_markdown(body))

    def test_over_cap_body_truncated_with_ellipsis(self):
        body = "<p>" + ("x" * (SNIPPET_CHAR_CAP + 100)) + "</p>"
        snippet = _article_snippet(body)
        self.assertEqual(len(snippet), SNIPPET_CHAR_CAP + 1)
        self.assertTrue(snippet.endswith("…"))
        # Only the trailing ellipsis is added; the first cap chars are content.
        self.assertNotIn("…", snippet[:-1])

    def test_at_cap_body_not_truncated(self):
        body = "<p>" + ("x" * SNIPPET_CHAR_CAP) + "</p>"
        snippet = _article_snippet(body)
        self.assertEqual(len(snippet), SNIPPET_CHAR_CAP)
        self.assertNotIn("…", snippet)

    def test_none_body_yields_empty_string(self):
        self.assertEqual(_article_snippet(""), "")


class TestSearchKnowledgeArticlesCommand(unittest.TestCase):
    """The built-in command registers and delegates to the helper."""

    def test_registered_under_name(self):
        self.assertIn("search_knowledge_articles", BUILTIN_COMMANDS)
        self.assertIs(
            BUILTIN_COMMANDS["search_knowledge_articles"],
            SearchKnowledgeArticlesCommand,
        )

    def test_execute_delegates_query_and_limit(self):
        client = MagicMock()
        target = (
            "odoo_sdk.commands.builtin.search_knowledge_articles."
            "search_knowledge_articles"
        )
        with patch(target, return_value=["shaped"]) as helper:
            result = SearchKnowledgeArticlesCommand(client).execute("vat", limit=7)
        self.assertEqual(result, ["shaped"])
        helper.assert_called_once_with(client, "vat", limit=7)

    def test_execute_defaults_are_read_only_search(self):
        client, executor = _client([_article()])
        SearchKnowledgeArticlesCommand(client).execute("vat")
        # A read-only probe + search_read, no writes of any kind.
        methods = [method for _, method, _, _ in executor.calls]
        self.assertEqual(methods, ["search_count", "search_read"])


class TestSearchKnowledgeArticlesToonEncoding(unittest.TestCase):
    """The list-of-dicts result encodes cleanly under the TOON output flag."""

    def test_result_toon_encodes(self):
        from odoo_sdk.mcp.server import TOON_OUTPUT_ENV, _to_toon

        client, _ = _client([_article(id=9, body="<p>needle</p>")])
        result = search_knowledge_articles(client, "needle")
        with patch.dict("os.environ", {TOON_OUTPUT_ENV: "1"}):
            out = _to_toon(result)
        self.assertIsInstance(out, str)
        self.assertIn("VAT rounding guide", out)
        self.assertIn("needle", out)


if __name__ == "__main__":
    unittest.main()
