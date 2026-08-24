"""Triage queue for unattributed events (issue #370, acceptance item 9).

An event ingested with an empty ``task_ids`` array is invisible to billing (the
derivation requires ``json_array_length(task_ids) > 0``), so an unattributed
meeting or email silently never bills. This module turns the raw unattributed
events for a window into the rows the TUI's triage mode displays, and renders
them as the plain-text lines the triage screen shows. Both are pure functions
tested without a terminal; the transitions in :mod:`~odoo_sdk.tui.app` own the
DB writes and the Textual screen in :mod:`~odoo_sdk.tui.textual_app` owns the
keystrokes.

**Series granularity.** Calendar meetings are ingested as a *tick series*: one
event per tick, every tick sharing a parent external-id prefix of the shape
``<parent>:tick:<iso>``, where ``<iso>`` is the tick's UTC ISO-8601 timestamp
(e.g. ``gcal:<event-id>:tick:2026-06-01T09:00:00+00:00``). That timestamp form is
canonical: the ingestion producer (``_tick_external_id`` in
:mod:`~odoo_sdk.adapters.external_sync`) writes it so a moved or resized meeting
yields a different id set, and this consumer matches what the producer emits.
Triage displays ONE row per series and assignment updates ``task_ids`` on EVERY
event of the series, so a whole meeting is attributed in a single action and the
choice survives a re-expansion (the ingestion side propagates ``task_ids`` across
reconciles). The series is recognized off the external-id pattern — nothing is
imported from the ingestion code — so this surface stays independent of how the
sources are named.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Sequence

from odoo_sdk.state import EventRecord

# A tick-series member's external id is ``<parent>:tick:<iso>`` where ``<iso>`` is
# the tick's UTC ISO-8601 timestamp — exactly what the ingestion producer emits.
# The suffix is matched structurally (date, time, optional fractional seconds,
# offset) rather than as "anything after the marker", so an unrelated id that
# merely contains ``:tick:`` is not mistaken for a series member. The captured
# group is the series key (``<parent>:tick:``) shared by every tick, so grouping
# on it collapses a whole expanded meeting into one row.
_TICK_TIMESTAMP = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})"
_SERIES_RE = re.compile(rf"^(.*:tick:){_TICK_TIMESTAMP}$")


def series_key(external_id: Optional[str]) -> Optional[str]:
    """Return the tick-series key for ``external_id``, or None if it is not a tick.

    A tick member matches ``<parent>:tick:<iso>``; its series key is the
    ``<parent>:tick:`` prefix every sibling tick shares. Any other external id
    (or ``None``) is not part of a series and triages as an individual event.
    """
    if not external_id:
        return None
    match = _SERIES_RE.match(external_id)
    return match.group(1) if match else None


@dataclass(frozen=True)
class TriageRow:
    """One triage line: a whole tick series, or a single unattributed event.

    :param display_key: What identifies the row to the user — the series key for
        a series, else the event's external id, else a synthetic ``event#<id>``.
    :param event_ids: Every event id the row covers; assignment writes them all.
    :param source: The representative (earliest) event's source.
    :param timestamp: The representative event's ISO timestamp.
    :param subject: The representative event's subject (may be empty).
    """

    display_key: str
    event_ids: tuple[int, ...]
    source: str
    timestamp: str
    subject: str

    @property
    def count(self) -> int:
        """Number of events this row assigns in one action."""
        return len(self.event_ids)


def _group_key(event: EventRecord) -> str:
    """Return the grouping key for ``event``: its series key, else a unique key.

    A tick member groups with its siblings under the shared series key; every
    other event gets a per-id key so it stays an individual row (two lone events
    never merge, even when both lack an external id).
    """
    key = series_key(event.external_id)
    return key if key is not None else f"\x00lone:{event.id}"


def _row_display_key(event: EventRecord) -> str:
    """Return the display key for the row anchored on representative ``event``."""
    key = series_key(event.external_id)
    if key is not None:
        return key
    return event.external_id or f"event#{event.id}"


def build_triage_rows(events: Sequence[EventRecord]) -> list[TriageRow]:
    """Collapse unattributed ``events`` into triage rows, series-first.

    ``events`` are assumed timestamp-ordered (as :meth:`get_unattributed_events`
    returns them). Tick-series members collapse into one row keyed on their shared
    series key; every other event is its own row. Row order follows first
    appearance, so the list stays in timestamp order and the representative event
    of each row is its earliest member.
    """
    grouped: dict[str, list[EventRecord]] = {}
    for event in events:
        grouped.setdefault(_group_key(event), []).append(event)
    rows: list[TriageRow] = []
    for members in grouped.values():
        head = members[0]
        rows.append(
            TriageRow(
                display_key=_row_display_key(head),
                event_ids=tuple(member.id for member in members),
                source=head.source,
                timestamp=head.timestamp.isoformat(),
                subject=head.subject,
            )
        )
    return rows


def row_line(row: TriageRow, selected: bool) -> str:
    """Render one triage row: a marker, source, count, timestamp, and subject."""
    marker = ">" if selected else " "
    count = f"x{row.count}" if row.count > 1 else "  "
    subject = row.subject or row.display_key
    return f"{marker} {row.source:<8} {count:<3} {row.timestamp[:19]}  {subject}"


def triage_body_lines(rows: Sequence[TriageRow], selected: int) -> list[str]:
    """Render the triage row list (one line per series/lone event).

    The header, the live task-id input echo, and the transient status line are
    separate widgets on the Textual triage screen, so only the body rows are
    composed here. An empty queue renders its explanatory placeholder instead.
    """
    if not rows:
        return [" nothing to triage — every event in window is attributed"]
    return [row_line(row, index == selected) for index, row in enumerate(rows)]
