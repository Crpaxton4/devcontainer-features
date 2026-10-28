"""Fetch a single project task by id."""

from dataclasses import dataclass
from typing import Any, Dict, Optional

from ..ports import Services
from ..registry import command


@dataclass(frozen=True)
class Args:
    task_id: int


@command(
    "get_todo",
    args=Args,
    description="Returns one project task by id, or None if not found.",
)
def handle(services: Services, args: Args) -> Optional[Dict[str, Any]]:
    """Return the ``project.task`` with ``task_id``, or ``None``."""
    fields_to_fetch = [
        "name",
        "project_id",
        "stage_id",
        "user_ids",
        "date_deadline",
    ]
    records = (
        services.client["project.task"]
        .search([("id", "=", args.task_id)], limit=1)
        .read(fields_to_fetch)
    )
    return records[0] if records else None
