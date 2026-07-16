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

#: Maximum number of characters kept from a *full* article body returned by
#: :func:`read_knowledge_article`. Far larger than :data:`SNIPPET_CHAR_CAP` (a
#: search preview) because this is the whole article, but a defensive upper bound
#: keeps a pathologically large body from overwhelming an LLM context window —
#: the same "cap the payload, flag the truncation" philosophy as the attachment
#: reader (#247). A body whose Markdown exceeds this is right-stripped, suffixed
#: with a single ellipsis (``"…"``) and reported with ``truncated=True``.
BODY_CHAR_CAP = 50_000


def assert_knowledge_available(client: OdooClient) -> None:
    """Raise ``ValueError`` when the ``knowledge.article`` model is unavailable.

    ``knowledge.article`` is an Odoo Enterprise model. This read-only probe
    counts matching ``ir.model`` rows; a zero count means the database is
    Community edition (or the Knowledge app is not installed), and a typed
    :class:`ValueError` with :data:`KNOWLEDGE_UNAVAILABLE_MESSAGE` is raised for
    the Epic C error boundary to format.
    """
    count = client.execute(
        "ir.model", "search_count", [("model", "=", "knowledge.article")]
    )
    if not count:
        raise ValueError(KNOWLEDGE_UNAVAILABLE_MESSAGE)


def _article_snippet(body: str) -> str:
    """Convert an article HTML body into a length-capped Markdown preview.

    The Markdown is capped at :data:`SNIPPET_CHAR_CAP` characters; an over-length
    preview is right-stripped and suffixed with a single ellipsis (``"…"``).
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
    order), and caps the result count at ``limit``. Returns
    ``{"id", "name", "snippet", "write_date"}`` dicts (``snippet`` capped at
    :data:`SNIPPET_CHAR_CAP`) and raises ``ValueError`` on a Community database.
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


def read_knowledge_article(client: OdooClient, article_id: int) -> dict[str, Any]:
    """Read one knowledge-base article's full body, HTML converted to Markdown.

    The Enterprise-only ``knowledge.article`` model is probed first (see
    :func:`assert_knowledge_available`), then the single record for ``article_id``
    is read. Its raw HTML ``body`` is converted to Markdown in full — the whole
    article, *not* the capped search snippet — and returned alongside the same
    identity fields the search tool surfaces (``id``, ``name``, ``write_date``)
    for a consistent shape across the two knowledge tools.

    As a defensive guard against a pathologically large article, the Markdown is
    capped at :data:`BODY_CHAR_CAP` characters: an over-cap body is right-stripped
    and suffixed with a single ellipsis (``"…"``), and ``truncated`` is set
    ``True`` (it is ``False`` for any body at or under the cap).

    Returns a ``{"id", "name", "body", "write_date", "truncated"}`` dict and
    raises ``ValueError`` on a Community database or when no article with
    ``article_id`` exists (``knowledge.article <id> not found``).
    """
    assert_knowledge_available(client)
    records = client.execute(
        "knowledge.article",
        "read",
        [article_id],
        fields=["id", "name", "body", "write_date"],
    )
    if not records:
        raise ValueError(f"knowledge.article {article_id} not found")
    record = records[0]
    markdown = html_to_markdown(record.get("body") or "")
    truncated = len(markdown) > BODY_CHAR_CAP
    if truncated:
        markdown = markdown[:BODY_CHAR_CAP].rstrip() + "…"
    return {
        "id": record["id"],
        "name": record["name"],
        "body": markdown,
        "write_date": record.get("write_date"),
        "truncated": truncated,
    }
