from dataclasses import dataclass
from typing import Any
from odoo_dev_core.services.knowledge import search_knowledge_articles

from ..ports import Services
from ..registry import command


@dataclass(frozen=True)
class Args:
    query: str
    limit: int = 10


@command(
    "search_knowledge_articles",
    args=Args,
    description="Search the Odoo Knowledge base (knowledge.article) by name or body. "
    "Matches the query case-insensitively against the article name OR its "
    "body (an OR ilike domain) and returns the most recently updated "
    "articles first (write_date desc), capped at ``limit``. Read-only; no "
    "articles are created or modified. Each result carries id, name, a "
    "HTML-stripped Markdown body snippet (capped at 500 characters), and "
    "write_date. knowledge.article is an Odoo Enterprise model: on a "
    "Community database the tool raises a clear error instead of returning "
    "results.",
)
def handle(services: Services, args: Args) -> list[dict[str, Any]]:
    """Return knowledge-base articles matching ``query``.

    :param query: Substring matched (``ilike``) against name OR body.
    :param limit: Maximum number of articles to return.
    :return: List of ``{"id", "name", "snippet", "write_date"}`` dicts.
    :raises ValueError: When ``knowledge.article`` is unavailable (the
        database is Odoo Community, not Enterprise).
    """
    return search_knowledge_articles(services.client, args.query, limit=args.limit)
