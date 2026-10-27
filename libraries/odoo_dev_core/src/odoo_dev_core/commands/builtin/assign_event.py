"""Built-in command that attributes tracker events to an Odoo task (triage write).

This is the *write half* of the triage surface (#370): the read half surfaces
events ingested with an empty ``task_ids`` (a meeting or email that could not be
confidently attributed), and this command attributes a whole selection — a lone
event or a full calendar series — to one task id in a single transaction, so the
now-attributed events satisfy ``json_array_length(task_ids) > 0`` and immediately
become derivable (and therefore billable).

Extracting the write behind a command (rather than letting the TUI call the store
directly) makes the operation surface-agnostic: registered as a built-in, it is
reusable by the TUI, an MCP atomic tool, and the CLI without any of them
re-implementing the validate/write business rule. Selecting *which* events to
attribute stays a caller concern (the TUI's highlighted row); the command owns
the atomic, validated write.
"""

from __future__ import annotations

from typing import Any

from ..command import Command
from ._registration import builtin_command


@builtin_command
class AssignEventCommand(Command):
    """Attribute one or more events to an Odoo task id in one transaction.

    Validates that ``task_id`` is a positive integer (via the store's atomic
    writer, which is the single source of that rule) and sets ``task_ids`` on
    every listed event so a whole series is attributed all-or-nothing. Returns
    the number of event rows updated for callers to report.
    """

    _name = "assign_event"
    _description = (
        "Attribute tracker events to an Odoo task id in a single transaction "
        "(the triage write): sets task_ids on every listed event so an "
        "unattributed meeting/email or a whole calendar series becomes derivable "
        "and billable. Validates a positive integer task id and returns the "
        "number of event rows updated. Selecting which events to assign is the "
        "caller's job; this command owns the atomic, validated write."
    )

    def execute(self, event_ids: list[int], task_id: int) -> dict[str, Any]:
        """Attribute ``event_ids`` to ``task_id`` atomically and report the count.

        :param event_ids: The event ids to (re)attribute; an empty list is a
            no-op that updates nothing.
        :param task_id: The Odoo task id to attribute the events to; must be a
            positive integer (no Odoo round-trip verifies it exists).
        :raises ValueError: When ``task_id`` is not a positive integer.
        :return: A summary dict with the ``task_id``, the ``event_ids`` written,
            and the number of event rows ``updated``.
        """
        ids = list(event_ids)
        updated = self.state.assign_event_task_ids(ids, task_id)
        return {"task_id": task_id, "event_ids": ids, "updated": updated}
