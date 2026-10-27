"""Attribute one or more events to an Odoo task id in one transaction.

Validates that ``task_id`` is a positive integer (via the store's atomic
writer, which is the single source of that rule) and sets ``task_ids`` on
every listed event so a whole series is attributed all-or-nothing. Returns
the number of event rows updated for callers to report."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..ports import Services
from ..registry import command


@dataclass(frozen=True)
class Args:
    event_ids: list[int]
    task_id: int


@command(
    "assign_event",
    args=Args,
    description="Attribute tracker events to an Odoo task id in a single transaction "
    "(the triage write): sets task_ids on every listed event so an "
    "unattributed meeting/email or a whole calendar series becomes derivable "
    "and billable. Validates a positive integer task id and returns the "
    "number of event rows updated. Selecting which events to assign is the "
    "caller's job; this command owns the atomic, validated write.",
)
def handle(services: Services, args: Args) -> dict[str, Any]:
    """Attribute ``event_ids`` to ``task_id`` atomically and report the count.

    :param event_ids: The event ids to (re)attribute; an empty list is a
        no-op that updates nothing.
    :param task_id: The Odoo task id to attribute the events to; must be a
        positive integer (no Odoo round-trip verifies it exists).
    :raises ValueError: When ``task_id`` is not a positive integer.
    :return: A summary dict with the ``task_id``, the ``event_ids`` written,
        and the number of event rows ``updated``.
    """
    ids = list(args.event_ids)
    updated = services.events.assign_event_task_ids(ids, args.task_id)
    return {"task_id": args.task_id, "event_ids": ids, "updated": updated}
