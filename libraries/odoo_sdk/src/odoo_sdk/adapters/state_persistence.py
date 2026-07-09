"""State-persistence adapter for the sessionization ETL.

Bridges the SQLite ``events`` / ``sessions`` tables (via
:class:`~odoo_sdk.state.LocalStateClient`) to the pure sessionization data
model. Reading turns :class:`EventRecord` rows into :class:`RawEvent` inputs;
writing turns computed :class:`TimeEntry` rows into :class:`SessionWindow` rows.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from odoo_sdk.sessionization import EventType, RawEvent, TimeEntry
from odoo_sdk.state import EventRecord, LocalStateClient, SessionWindow

# EventRecord.source strings map onto the pure EventType enum.
_SOURCE_TO_EVENT_TYPE = {
    "commit": EventType.COMMIT,
    "merge": EventType.MERGE,
    "review": EventType.REVIEW,
    "agent": EventType.AGENT,
}
_EVENT_TYPE_TO_SOURCE = {value: key for key, value in _SOURCE_TO_EVENT_TYPE.items()}


def event_record_to_raw_event(record: EventRecord) -> RawEvent:
    """Convert a persisted :class:`EventRecord` to a pure :class:`RawEvent`."""
    payload = record.payload or {}
    return RawEvent(
        timestamp=record.timestamp,
        task_ids=list(record.task_ids),
        repo=record.repo,
        pr_num=record.pr_num,
        event_type=_SOURCE_TO_EVENT_TYPE.get(record.source, EventType.COMMIT),
        branch=record.branch,
        is_release=len(record.task_ids) > 1,
        subject=record.subject,
        pr_title=payload.get("pr_title", ""),
        pr_body=payload.get("pr_body", ""),
    )


def raw_event_to_event_record(event: RawEvent) -> EventRecord:
    """Convert a pure :class:`RawEvent` to a persistable :class:`EventRecord`."""
    return EventRecord(
        id=None,
        source=_EVENT_TYPE_TO_SOURCE.get(event.event_type, "commit"),
        timestamp=event.timestamp,
        task_ids=list(event.task_ids),
        repo=event.repo,
        pr_num=event.pr_num,
        branch=event.branch,
        subject=event.subject,
        payload={"pr_title": event.pr_title, "pr_body": event.pr_body},
    )


def time_entry_to_session_window(entry: TimeEntry) -> SessionWindow:
    """Convert a computed :class:`TimeEntry` to a :class:`SessionWindow`."""
    return SessionWindow(
        id=None,
        task_id=entry.task_id,
        repo=entry.repo,
        started_at=entry.start,
        ended_at=entry.end,
        strategy_name=entry.strategy_name,
        category=entry.strategy_category,
        pr_num=entry.pr_num,
    )


def load_raw_events(
    state: LocalStateClient,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> list[RawEvent]:
    """Read stored events (optionally range-bounded) as pure :class:`RawEvent`."""
    return [
        event_record_to_raw_event(record)
        for record in state.get_events(start, end)
    ]


def persist_session_windows(
    state: LocalStateClient,
    entries: list[TimeEntry],
    *,
    replace: bool = True,
) -> list[SessionWindow]:
    """Write computed entries to the ``sessions`` table.

    When ``replace`` is set the table is cleared first so a run yields a fresh
    materialization rather than accumulating duplicates.
    """
    if replace:
        state.clear_session_windows()
    return [
        state.add_session_window(time_entry_to_session_window(entry))
        for entry in entries
    ]
