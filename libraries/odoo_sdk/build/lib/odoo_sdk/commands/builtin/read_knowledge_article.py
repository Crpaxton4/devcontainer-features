from typing import Any

from ..command import Command
from ._registration import builtin_command
from odoo_sdk.utilities.knowledge import read_knowledge_article


@builtin_command
class ReadKnowledgeArticleCommand(Command):
    _name = "read_knowledge_article"
    _description = (
        "Read one Odoo Knowledge article (knowledge.article) by id and return "
        "its full body converted from HTML to Markdown. Read-only; no article "
        "is created or modified. The result carries id, name, the full Markdown "
        "body (capped at 50000 characters with a ``truncated`` flag when the "
        "body is longer), and write_date. An unknown id raises a clear "
        "\"knowledge.article <id> not found\" error. knowledge.article is an "
        "Odoo Enterprise model: on a Community database the tool raises a clear "
        "error instead of returning content."
    )

    def execute(self, article_id: int) -> dict[str, Any]:
        """Return the full Markdown body of a single knowledge-base article.

        :param article_id: The ``knowledge.article`` id to read.
        :return: A ``{"id", "name", "body", "write_date", "truncated"}`` dict.
        :raises ValueError: When ``knowledge.article`` is unavailable (the
            database is Odoo Community, not Enterprise), or when no article
            exists with ``article_id``.
        """
        return read_knowledge_article(self._client, article_id)
