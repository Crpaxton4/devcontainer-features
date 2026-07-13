"""Knowledge-base (``knowledge.article``) read-only helpers.

``knowledge.article`` ships only with Odoo Enterprise, so every entry point here
first probes the model's existence via :func:`assert_knowledge_available` and
raises a clear, typed :class:`ValueError` on Community databases. The Epic C
error boundary catches that ``ValueError`` and formats it into the uniform
``{"error": {"type", "message"}}`` payload, so LLM callers see a stable message
rather than a raw traceback.

The probe and the search/shaping helpers are deliberately factored apart from the
built-in command so a sibling tool (``read_knowledge_article``, #249) can import
:func:`assert_knowledge_available` and reuse it rather than duplicating the
capability check.
"""

from typing import Any

from odoo_sdk.client import OdooClient

from .html import html_to_markdown

#: Exact, stable error message raised when ``knowledge.article`` is absent
#: (Odoo Community, or the Knowledge app not installed). Pinned so callers and
#: tests can match it verbatim; the Epic C boundary surfaces it as
#: ``{"error": {"type": "ValueError", "message": <this>}}``.
KNOWLEDGE_UNAVAILABLE_MESSAGE = (
    "knowledge.article model not available (Odoo Enterprise required)"
)

#: Maximum number of characters kept from an article body preview. The HTML body
#: is converted to Markdown and truncated to this many characters; a truncated
#: snippet is right-stripped and suffixed with a single ellipsis (``"…"``).
SNIPPET_CHAR_CAP = 500


def assert_knowledge_available(client: OdooClient) -> None:
    """Raise ``ValueError`` when the ``knowledge.article`` model is unavailable.

    ``knowledge.article`` is an Odoo Enterprise model. This read-only probe
    counts matching ``ir.model`` rows; a zero count means the database is
    Community edition (or the Knowledge app is not installed), and a typed
    :class:`ValueError` with :data:`KNOWLEDGE_UNAVAILABLE_MESSAGE` is raised for
    the Epic C error boundary to format.

    :param client: The Odoo API client.
    :type client: OdooClient
    :raises ValueError: With :data:`KNOWLEDGE_UNAVAILABLE_MESSAGE` when the model
        does not exist.
    :return: None.
    :rtype: None
    """
    count = client.execute(
        "ir.model", "search_count", [("model", "=", "knowledge.article")]
    )
    if not count:
        raise ValueError(KNOWLEDGE_UNAVAILABLE_MESSAGE)


def _article_snippet(body: str) -> str:
    """Convert an article HTML body into a length-capped Markdown preview.

    :param body: Raw HTML body of the article (may be empty/falsy).
    :type body: str
    :return: HTML-stripped Markdown, capped at :data:`SNIPPET_CHAR_CAP`
        characters; an over-length preview is right-stripped and ends in ``"…"``.
    :rtype: str
    """
    markdown = html_to_markdown(body or "")
    if len(markdown) > SNIPPET_CHAR_CAP:
        return markdown[:SNIPPET_CHAR_CAP].rstrip() + "…"
    return markdown


def search_knowledge_articles(
    client: OdooClient, query: str, limit: int = 10
) -> list[dict[str, Any]]:
    """Search knowledge-base articles by name or body, newest first.

    The Enterprise-only ``knowledge.article`` model is probed first (see
    :func:`assert_knowledge_available`). The search matches ``query``
    case-insensitively (``ilike``) against either the article ``name`` **or** its
    ``body`` (an OR domain), orders the most recently written articles first
    (``write_date desc``, tie-broken by ``id desc`` for a fully deterministic
    order), and caps the result count at ``limit``.

    :param client: The Odoo API client.
    :type client: OdooClient
    :param query: Substring matched (``ilike``) against name OR body.
    :type query: str
    :param limit: Maximum number of articles to return.
    :type limit: int
    :return: List of ``{"id", "name", "snippet", "write_date"}`` dicts, where
        ``snippet`` is the HTML-stripped body preview capped at
        :data:`SNIPPET_CHAR_CAP` characters.
    :rtype: list[dict[str, Any]]
    :raises ValueError: When ``knowledge.article`` is unavailable (Community).
    """
    assert_knowledge_available(client)
    records = client.execute(
        "knowledge.article",
        "search_read",
        ["|", ("name", "ilike", query), ("body", "ilike", query)],
        fields=["id", "name", "body", "write_date"],
        order="write_date desc, id desc",
        limit=limit,
    )
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "snippet": _article_snippet(r.get("body", "")),
            "write_date": r.get("write_date"),
        }
        for r in records
    ]
