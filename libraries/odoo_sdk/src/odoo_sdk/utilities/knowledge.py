"""Knowledge-base (``knowledge.article``) read-only helpers.

``knowledge.article`` ships only with Odoo Enterprise. Availability is determined
by attempting the real ``knowledge.article`` query and classifying whatever fault
comes back (see :func:`_knowledge_query`): a fault meaning the model itself is
absent is translated to a clear, typed :class:`ValueError` for Community
databases, while every other fault — crucially a permission denial — propagates
unchanged so it keeps its own type.

Critically, *no* code path here ever touches ``ir.model``. That administrative
table must never be granted to a least-privileged, read-only service account, so
a pre-flight probe against it (the original #444 defect) would either fail for
every correctly-scoped account or force a privilege escalation. The Epic C error
boundary catches the ``ValueError`` (and any propagated ``OdooError``) and formats
it into the uniform ``{"error": {"type", "message"}}`` payload, so LLM callers see
a stable, typed message rather than a raw traceback.

The query/shaping helpers are deliberately factored apart from the built-in
command so a sibling tool (``read_knowledge_article``, #249) can import
:func:`_knowledge_query` and reuse the capability handling rather than duplicating
it.
"""

from typing import Any

from odoo_sdk.client import OdooClient
from odoo_sdk.transport.errors import (
    OdooAccessError,
    OdooAuthenticationError,
    OdooError,
    OdooMissingRecordError,
    OdooValidationError,
)

from .html import html_to_markdown

#: Exact, stable error message raised when ``knowledge.article`` is absent
#: (Odoo Community, or the Knowledge app not installed). Pinned so callers and
#: tests can match it verbatim; the Epic C boundary surfaces it as
#: ``{"error": {"type": "ValueError", "message": <this>}}``.
KNOWLEDGE_UNAVAILABLE_MESSAGE = (
    "knowledge.article model not available (Odoo Enterprise required)"
)

#: Classified faults that are their own actionable failures and must never be
#: relabeled as the Community "model unavailable" error: a permission/auth denial
#: (the exact #444 bug — a least-privileged account being told it lacks access),
#: a value/argument rejection, or a missing *record* (a bad article id, not a
#: missing model). Each is excluded by class before the text check below, so it
#: propagates unchanged for the MCP boundary to render with its own type.
_PASS_THROUGH_ERRORS = (
    OdooAccessError,
    OdooAuthenticationError,
    OdooValidationError,
    OdooMissingRecordError,
)

#: Case-insensitive substrings marking an Odoo fault as a *missing model* rather
#: than a generic server error. When ``knowledge.article`` is absent, Odoo
#: resolves ``env["knowledge.article"]`` to a ``KeyError`` that surfaces across
#: transports as an unmapped server fault carrying one of these markers. Permission,
#: auth, validation, and missing-*record* faults are already excluded by class
#: (:data:`_PASS_THROUGH_ERRORS`) before this text check runs, so these markers
#: only have to separate "no such model" from an unrelated server fault. Deliberately
#: *not* sufficient alone — see :data:`_MODEL_NAME_MARKERS` — because phrases like
#: "does not exist" also appear in ordinary business ``UserError`` messages (e.g. "the
#: linked document does not exist") that have nothing to do with the model being
#: absent, and misreading one as the Community edition message would hide the real
#: failure from the caller.
_MODEL_ABSENT_MARKERS = (
    "keyerror",
    "does not exist",
    "doesn't exist",
    "invalid model",
    "unknown model",
    "model not found",
    "no such model",
)

#: Case-insensitive spellings of the model name itself that must co-occur with a
#: :data:`_MODEL_ABSENT_MARKERS` marker before a fault is classified as "model
#: absent". A genuine missing-model fault names the model it failed to resolve
#: (``KeyError: 'knowledge.article'``, ``Invalid model name 'knowledge.article'``);
#: an unrelated business error raised *from within* ``knowledge.article`` (a
#: ``UserError`` about a broken cross-reference, a Postgres fault on an unrelated
#: table) will not. Both the dotted Odoo model name and its underscored SQL table
#: form are checked since faults can surface either spelling.
_MODEL_NAME_MARKERS = ("knowledge.article", "knowledge_article")

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


def _is_missing_model_error(exc: OdooError) -> bool:
    """Return whether ``exc`` means ``knowledge.article`` does not exist.

    A permission/auth denial, a validation rejection, or a missing *record* is a
    distinct, actionable failure (:data:`_PASS_THROUGH_ERRORS`) and is *never*
    treated as a missing model — this is what keeps a least-privileged account's
    access error from being mislabeled as an edition error (#444). Of what remains,
    only a fault whose text carries *both* a :data:`_MODEL_ABSENT_MARKERS` marker
    *and* a :data:`_MODEL_NAME_MARKERS` mention of the model itself counts as the
    model genuinely being absent (Community edition) — requiring both keeps an
    unrelated business ``UserError`` (e.g. "the linked document does not exist")
    from being misread as an edition error and hiding the real failure.
    """
    if isinstance(exc, _PASS_THROUGH_ERRORS):
        return False
    haystack = f"{exc} {exc.fault_string or ''}".lower()
    return any(marker in haystack for marker in _MODEL_ABSENT_MARKERS) and any(
        name in haystack for name in _MODEL_NAME_MARKERS
    )


def _knowledge_query(
    client: OdooClient, method: str, *args: Any, **kwargs: Any
) -> Any:
    """Issue one read-only ``knowledge.article`` call, mapping a missing model.

    Runs the real ``knowledge.article`` query directly — there is deliberately no
    ``ir.model`` pre-flight probe, because that administrative table must never be
    granted to a least-privileged service account (#444). A fault meaning the
    model itself is absent (Community edition, or the Knowledge app uninstalled)
    is translated to the typed :data:`KNOWLEDGE_UNAVAILABLE_MESSAGE`
    ``ValueError``; every other fault — crucially an
    :class:`~odoo_sdk.transport.errors.OdooAccessError` permission denial —
    propagates unchanged so it is surfaced with its own type rather than being
    relabeled as an edition error.
    """
    try:
        return client.execute("knowledge.article", method, *args, **kwargs)
    except OdooError as exc:
        if _is_missing_model_error(exc):
            raise ValueError(KNOWLEDGE_UNAVAILABLE_MESSAGE) from exc
        raise


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

    The search runs directly against the Enterprise-only ``knowledge.article``
    model (see :func:`_knowledge_query`), matching ``query`` case-insensitively
    (``ilike``) against either the article ``name`` **or** its ``body`` (an OR
    domain), ordering the most recently written articles first (``write_date
    desc``, tie-broken by ``id desc`` for a fully deterministic order), and
    capping the result count at ``limit``. Returns
    ``{"id", "name", "snippet", "write_date"}`` dicts (``snippet`` capped at
    :data:`SNIPPET_CHAR_CAP`) and raises ``ValueError`` on a Community database; a
    permission error propagates unchanged with its own type.
    """
    records = _knowledge_query(
        client,
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

    The single record for ``article_id`` is read directly from the Enterprise-only
    ``knowledge.article`` model (see :func:`_knowledge_query`). Its raw HTML
    ``body`` is converted to Markdown in full — the whole article, *not* the
    capped search snippet — and returned alongside the same identity fields the
    search tool surfaces (``id``, ``name``, ``write_date``) for a consistent shape
    across the two knowledge tools.

    As a defensive guard against a pathologically large article, the Markdown is
    capped at :data:`BODY_CHAR_CAP` characters: an over-cap body is right-stripped
    and suffixed with a single ellipsis (``"…"``), and ``truncated`` is set
    ``True`` (it is ``False`` for any body at or under the cap).

    Returns a ``{"id", "name", "body", "write_date", "truncated"}`` dict and
    raises ``ValueError`` on a Community database or when no article with
    ``article_id`` exists (``knowledge.article <id> not found``); a permission
    error propagates unchanged with its own type.
    """
    records = _knowledge_query(
        client,
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
