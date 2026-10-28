"""Return the UID of the authenticated Odoo user."""

from dataclasses import dataclass

from ..ports import Services
from ..registry import command


@dataclass(frozen=True)
class Args:
    pass


@command(
    "get_uid",
    args=Args,
    description="Get the UID of the current user.",
)
def handle(services: Services, args: Args) -> int:
    """Return the authenticated user's Odoo id."""
    return services.client.uid
