"""State-persistence adapter for the sessionization ETL.

Bridges the SQLite ``events`` table (via
:class:`~odoo_sdk.state.LocalStateClient`) to the pure sessionization data
model. Reading turns :class:`EventRecord` rows into :class:`RawEvent` inputs used
by the read-only gap-sweep optimizer and the TUI export. Sessions are derived
from the events at query time, so there is no session materialization here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from odoo_sdk.sessionization import EventType, RawEvent
from odoo_sdk.state import EventRecord, LocalStateClient

# Prefix marking an EventRecord.source as originating from a Claude Code hook.
_CLAUDE_SOURCE_PREFIX = "claude:"

# EventRecord.source strings map onto the pure EventType enum.
_SOURCE_TO_EVENT_TYPE = {
    "commit": EventType.COMMIT,
    "merge": EventType.MERGE,
    "review": EventType.REVIEW,
    "agent": EventType.AGENT,
}
# Reverse map. ``CLAUDE_HOOK`` has no single canonical source string (its
# sources are the open-ended ``claude:<HookName>`` family), so it is given the
# synthetic placeholder ``"claude:hook"``. This reverse entry is only reachable
# when a synthetic ``RawEvent`` carrying ``EventType.CLAUDE_HOOK`` is written
# back out; persisted hook events always keep their original ``claude:<...>``
# source string and never round-trip through here.
_EVENT_TYPE_TO_SOURCE = {value: key for key, value in _SOURCE_TO_EVENT_TYPE.items()}
_EVENT_TYPE_TO_SOURCE[EventType.CLAUDE_HOOK] = "claude:hook"


class UnknownEventSourceError(ValueError):
    """Raised when an :class:`EventRecord` source maps to no known event type.

    Unknown sources previously fell back silently to :class:`EventType.COMMIT`,
    masquerading as commits and corrupting sessionization. Mapping now fails
    loudly so a misspelled or unregistered source is surfaced, not buried.
    """


def source_to_event_type(source: str) -> EventType:
    """Resolve an :class:`EventRecord` source string to its :class:`EventType`.

    Known sources (``commit``/``merge``/``review``/``agent``) map directly. Any
    ``claude:<HookName>`` source resolves to :class:`EventType.CLAUDE_HOOK`.
    Anything else raises :class:`UnknownEventSourceError` rather than silently
    defaulting to a commit.
    """
    known = _SOURCE_TO_EVENT_TYPE.get(source)
    if known is not None:
        return known
    if source.startswith(_CLAUDE_SOURCE_PREFIX):
        return EventType.CLAUDE_HOOK
    raise UnknownEventSourceError(f"unknown event source {source!r}")


def event_record_to_raw_event(record: EventRecord) -> RawEvent:
    """Convert a persisted :class:`EventRecord` to a pure :class:`RawEvent`."""
    payload = record.payload or {}
    return RawEvent(
        timestamp=record.timestamp,
        task_ids=list(record.task_ids),
        repo=record.repo,
        pr_num=record.pr_num,
        event_type=source_to_event_type(record.source),
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
