"""State-persistence adapter for the sessionization ETL.

Bridges the SQLite ``events`` / ``sessions`` tables (via
:class:`~odoo_sdk.state.LocalStateClient`) to the pure sessionization data
model. Reading turns :class:`EventRecord` rows into :class:`RawEvent` inputs;
writing turns computed :class:`TimeEntry` rows into :class:`SessionWindow` rows.

It also drives *incremental* ingestion: for a batch of new events it loads only
the affected groups' local neighborhoods, runs the pure incremental sessionizer
(:mod:`odoo_sdk.sessionization.incremental`), and persists the session and
event → session link deltas. Its cost scales with the batch size and the number
of affected sessions, never with total history.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from odoo_sdk.sessionization import (
    DEFAULT_SESSION_STRATEGY_CONFIGS,
    EventType,
    IncrementalResult,
    LinkDelta,
    RawEvent,
    SessionEvent,
    SessionState,
    SessionStrategyConfig,
    TimeEntry,
    resolve_repo,
    sessionize_group,
)
from odoo_sdk.state import EventRecord, LocalStateClient, SessionWindow

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


# ── Incremental ingestion ────────────────────────────────────────────────────
#
# Only session-kind (windowed) strategies undergo gap-based global
# sessionization; fixed-kind strategies emit one entry per event and are the
# analysis/optimizer path's concern, so their events are skipped here.


def _session_kind_by_source(
    settings_rows: tuple[SessionStrategyConfig, ...],
) -> dict[str, SessionStrategyConfig]:
    """Map each event source string to its owning session-kind strategy row.

    Only ``strategy_kind == "session"`` rows are included, so events owned by a
    fixed strategy (merge/review) are absent and therefore excluded from
    incremental sessionization.
    """
    by_source: dict[str, SessionStrategyConfig] = {}
    for row in settings_rows:
        if row.strategy_kind != "session":
            continue
        for event_type in row.event_types:
            source = _EVENT_TYPE_TO_SOURCE.get(event_type)
            if source is not None:
                by_source[source] = row
    return by_source


def _record_to_session_event(
    record: EventRecord, row: SessionStrategyConfig
) -> SessionEvent:
    """Build a :class:`SessionEvent` from a persisted record and its strategy."""
    task_id = record.task_ids[0] if record.task_ids else "UNKNOWN"
    return SessionEvent(
        id=record.id,  # type: ignore[arg-type]
        timestamp=record.timestamp,
        task_id=task_id,
        repo=resolve_repo(record.repo),
        strategy_name=row.name,
        category=row.category,
        pr_num=record.pr_num,
    )


def _group_records(
    records: list[EventRecord],
    by_source: dict[str, SessionStrategyConfig],
) -> dict[tuple[str, str, str], list[SessionEvent]]:
    """Group session-eligible records by ``(task_id, repo, strategy_name)``.

    Records whose source is not owned by a session-kind strategy, or that lack a
    persisted id, are skipped (never grouped, never linked).
    """
    groups: dict[tuple[str, str, str], list[SessionEvent]] = {}
    for record in records:
        row = by_source.get(record.source)
        if row is None or record.id is None:
            continue
        event = _record_to_session_event(record, row)
        key = (event.task_id, event.repo, event.strategy_name)
        groups.setdefault(key, []).append(event)
    return groups


def _load_neighborhood(
    state: LocalStateClient,
    key: tuple[str, str, str],
    events: list[SessionEvent],
    gap_secs: int,
) -> tuple[list[SessionState], list[SessionEvent]]:
    """Load the sessions and events a group's new events could touch.

    Existing sessions are always more than ``gap_secs`` apart, so only those
    within one gap of the new-event span can merge with (or absorb) the batch.
    Querying ``[min_ts - gap, max_ts + gap]`` on the group's own rows loads that
    complete local neighborhood and nothing more, keeping the work bounded. Each
    such session's own linked events are returned so the pure sessionizer sees
    the full multiset it must partition.

    :return: The neighborhood's existing sessions and their linked events.
    """
    task_id, repo, strategy_name = key
    lo = min(event.timestamp for event in events) - timedelta(seconds=gap_secs)
    hi = max(event.timestamp for event in events) + timedelta(seconds=gap_secs)
    windows = state.get_sessions_overlapping(
        lo, hi, task_id=task_id, repo=repo, strategy_name=strategy_name
    )
    sessions: list[SessionState] = []
    existing_events: list[SessionEvent] = []
    row = _strategy_row_for(key)
    for window in windows:
        linked = state.get_events_for_session(window.id)  # type: ignore[arg-type]
        sessions.append(_window_to_session_state(window, linked))
        existing_events.extend(
            _record_to_session_event(record, row)
            for record in linked
            if record.id is not None
        )
    return sessions, existing_events


def _strategy_row_for(key: tuple[str, str, str]) -> SessionStrategyConfig:
    """Return a session-kind strategy row matching a group's strategy name.

    Neighborhood events are rehydrated only to feed the partition (timestamp +
    group key), so any session-kind row with the group's name suffices; the
    default configs are consulted so callers need not thread rows through here.
    """
    for row in DEFAULT_SESSION_STRATEGY_CONFIGS:
        if row.strategy_kind == "session" and row.name == key[2]:
            return row
    # Fallback: synthesize a minimal session row carrying the group's name.
    return SessionStrategyConfig(
        name=key[2],
        category="Development",
        event_types=(),
        strategy_kind="session",
        group_keys=("strategy", "repo", "task_id"),
    )


def _window_to_session_state(
    window: SessionWindow, linked: list[EventRecord]
) -> SessionState:
    """Rehydrate a persisted window and its linked event ids into a session."""
    return SessionState(
        id=window.id,
        task_id=window.task_id,
        repo=window.repo,
        started_at=window.started_at,
        ended_at=window.ended_at,
        strategy_name=window.strategy_name,
        category=window.category,
        pr_num=window.pr_num,
        event_ids=tuple(e.id for e in linked if e.id is not None),
    )


def _session_state_to_window(session: SessionState) -> SessionWindow:
    """Convert a computed :class:`SessionState` to a persistable window."""
    return SessionWindow(
        id=session.id,
        task_id=session.task_id,
        repo=session.repo,
        started_at=session.started_at,
        ended_at=session.ended_at,
        strategy_name=session.strategy_name,
        category=session.category,
        pr_num=session.pr_num,
    )


def _persist_group_result(
    state: LocalStateClient, result: IncrementalResult
) -> int:
    """Persist one group's session/link deltas; return the resolved session ids.

    Order matters: created rows are written first (so their ids exist), existing
    rows are updated in place, and removed rows are deleted last (the FK nulls
    any straggler links). Then each event's ``session_id`` is set from the
    resolved link deltas, so every ingested event ends linked to exactly one
    session. The count of ``created`` rows written is returned.
    """
    new_ids = [
        state.add_session_window(_session_state_to_window(session)).id
        for session in result.created
    ]
    for session in result.sessions:
        if session.id is not None:
            state.update_session_window(_session_state_to_window(session))
    for session_id in result.deleted_ids:
        state.delete_session_window(session_id)
    _apply_links(state, result.links, new_ids)
    return len(new_ids)


def _apply_links(
    state: LocalStateClient,
    links: list[LinkDelta],
    new_ids: list[int],
) -> None:
    """Write each event's resolved ``session_id`` from the link deltas."""
    for link in links:
        session_id = link.session_ref
        if session_id is None and link.new_session_index is not None:
            session_id = new_ids[link.new_session_index]
        state.set_event_session(link.event_id, session_id)


def ingest_events_incrementally(
    state: LocalStateClient,
    records: list[EventRecord],
    gap_secs: int,
    *,
    settings_rows: tuple[SessionStrategyConfig, ...] = DEFAULT_SESSION_STRATEGY_CONFIGS,
) -> int:
    """Ingest ``records`` into the global sessionization incrementally.

    Groups the new records by ``(task, repo, strategy)``, loads only each
    affected group's local neighborhood of existing sessions, runs the pure
    incremental sessionizer, and persists the session and event → session link
    deltas. Repo-less agent events group under the reserved sentinel repo.

    :param state: The SQLite-backed local state store.
    :param records: The persisted events to ingest (each must have a real id).
    :param gap_secs: The fixed inactivity gap (session identity constant).
    :param settings_rows: Strategy configuration; only session-kind rows drive
        incremental sessionization.
    :return: The number of new session rows created across all groups.
    :rtype: int
    """
    by_source = _session_kind_by_source(settings_rows)
    groups = _group_records(records, by_source)
    created = 0
    for key, events in groups.items():
        existing, existing_events = _load_neighborhood(state, key, events, gap_secs)
        # New events last so a re-ingested id wins over its stale linked copy.
        result = sessionize_group(existing, existing_events + events, gap_secs)
        created += _persist_group_result(state, result)
    return created
