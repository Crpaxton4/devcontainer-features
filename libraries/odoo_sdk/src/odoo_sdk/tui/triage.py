"""Triage queue for unattributed events (issue #370, acceptance item 9).

An event ingested with an empty ``task_ids`` array is invisible to billing (the
derivation requires ``json_array_length(task_ids) > 0``), so an unattributed
meeting or email silently never bills. This module turns the raw unattributed
events for a window into the rows the TUI's triage mode displays, and composes
the triage screen. Both are pure functions tested without a terminal; the driver
in :mod:`~odoo_sdk.tui.app` owns the keystrokes and the DB writes.

**Series granularity.** Calendar meetings are ingested as a *tick series*: one
event per tick, every tick sharing a parent external-id prefix of the shape
``<parent>:tick:<n>`` (e.g. ``gcal:<event-id>:tick:0`` … ``:tick:12``). Triage
displays ONE row per series and assignment updates ``task_ids`` on EVERY event of
the series, so a whole meeting is attributed in a single action and the choice
survives a re-expansion (the ingestion side propagates ``task_ids`` across
reconciles). The series is recognized generically off the external-id pattern —
nothing is imported from the ingestion code — so this surface stays independent
of how the sources are named.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Sequence

from odoo_sdk.state import EventRecord

from .frame import Frame, _fit

# A tick-series member's external id is ``<parent>:tick:<n>`` where ``<n>`` is the
# tick index. The captured group is the series key (``<parent>:tick:``) shared by
# every tick, so grouping on it collapses a whole expanded meeting into one row.
_SERIES_RE = re.compile(r"^(.*:tick:)\d+$")

_TRIAGE_FOOTER = " ↑/↓ select  0-9 task id  ⏎ assign  s:skip  q:back "


def series_key(external_id: Optional[str]) -> Optional[str]:
    """Return the tick-series key for ``external_id``, or None if it is not a tick.

    A tick member matches ``<parent>:tick:<n>``; its series key is the
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


def _row_line(row: TriageRow, selected: bool, width: int) -> str:
    """Render one triage row: a marker, source, count, timestamp, and subject."""
    marker = ">" if selected else " "
    count = f"x{row.count}" if row.count > 1 else "  "
    subject = row.subject or row.display_key
    line = f"{marker} {row.source:<8} {count:<3} {row.timestamp[:19]}  {subject}"
    return _fit(line, width)


def compose_triage_frame(
    rows: Sequence[TriageRow],
    selected: int,
    task_input: str,
    width: int,
    height: int,
) -> Frame:
    """Compose the triage screen: a header, the row list, an input line, a footer.

    The transient status/confirmation line is deliberately NOT rendered here: the
    driver's ``_draw`` paints ``state.status`` on the bottom screen row for every
    mode, exactly as it does for the timeline view, so composing it into the frame
    too would print it twice on adjacent lines.

    :param rows: The triage rows to list (one per series or lone event).
    :param selected: Index of the highlighted row.
    :param task_input: The task id being typed (digits only), shown live.
    :param width: Terminal column count.
    :param height: Terminal row count.
    :return: A :class:`Frame` of exactly ``height`` rows each ``width`` wide.
    """
    header = _fit(f" triage — {len(rows)} unattributed item(s)", width)
    footer = _fit(_TRIAGE_FOOTER, width)
    prompt = _fit(f" task id > {task_input}", width)
    # Body rows are everything between the header and the pinned prompt/footer.
    body_height = max(0, height - 3)
    if rows:
        body = [_row_line(row, i == selected, width) for i, row in enumerate(rows)]
    else:
        body = [_fit(" nothing to triage — every event in window is attributed", width)]
    body = body[:body_height] + [_fit("", width)] * (body_height - len(body))
    composed = [header, *body, prompt, footer]
    return Frame(rows=composed[:height], width=width, height=height)
