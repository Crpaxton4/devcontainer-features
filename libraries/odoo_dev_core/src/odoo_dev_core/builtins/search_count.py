"""Count records matching an Odoo domain without retrieving any of them."""

from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

from ..ports import Services
from ..registry import command


@dataclass(frozen=True)
class Args:
    model: str
    domain: Optional[List[Tuple[str, str, Any]]] = None


@command(
    "search_count",
    args=Args,
    description="Counts records on a model matching an optional domain filter.",
)
def handle(services: Services, args: Args) -> int:
    """Return how many ``model`` records match ``domain`` (``None`` = all)."""
    return services.client[args.model].search_count(args.domain)
