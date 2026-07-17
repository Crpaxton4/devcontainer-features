from typing import Any, List, Optional, Tuple

from ..command import Command
from ._registration import builtin_command


@builtin_command
class SearchCountCommand(Command):
    """Count records matching an Odoo domain without retrieving any of them."""

    _name = "search_count"
    _description = "Counts records on a model matching an optional domain filter."

    def execute(
        self,
        model: str,
        domain: Optional[List[Tuple[str, str, Any]]] = None,
    ) -> int:
        """Return how many ``model`` records match ``domain`` (``None`` = all)."""
        return self._client[model].search_count(domain)
