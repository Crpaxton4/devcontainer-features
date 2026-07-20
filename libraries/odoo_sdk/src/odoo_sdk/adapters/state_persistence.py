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

# Payload marker set on synthetic calendar meeting ticks (#370). Ticks are real
# ``events`` rows that DO participate in session derivation (that is how a meeting
# becomes one session), but the gap-sweep analysis must not see them: 13 rows per
# meeting would dominate the raw-event population and bias the recommended gap
# toward the tick train rather than the person's real work rhythm.
_SYNTHETIC_PAYLOAD_KEY = "synthetic"

# EventRecord.source strings map onto the pure EventType enum.
_SOURCE_TO_EVENT_TYPE = {
    "commit": EventType.COMMIT,
    "merge": EventType.MERGE,
    "review": EventType.REVIEW,
    "agent": EventType.AGENT,
    "chatter": EventType.CHATTER,
    "calendar": EventType.CALENDAR,
    "email": EventType.EMAIL,
}

# Non-canonical source strings that share an already-mapped EventType. ``comment``
# (authored PR/issue comments, ``gh:comment:<id>``, written by
# ``external_sync._comment_event``) is a review-family source to the SQL
# derivation — ``_REVIEW_SOURCE_PREDICATE = "source IN ('review', 'comment')"`` —
# so the Python engine must resolve it to the same ``REVIEW`` type or the two
# engines diverge (a single comment event used to raise
# :class:`UnknownEventSourceError` and poison the whole ETL window). Aliases are
# deliberately kept OUT of the reverse map: ``REVIEW`` writes back out under its
# canonical ``"review"`` source.
_SOURCE_ALIASES = {
    "comment": EventType.REVIEW,
}

# Every source string the read path accepts: canonical sources plus aliases.
_RESOLVABLE_SOURCES = {**_SOURCE_TO_EVENT_TYPE, **_SOURCE_ALIASES}

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

    Known sources (``commit``/``merge``/``review``/``agent``/``chatter``) map
    directly, as does the ``comment`` alias for the review family. Any
    ``claude:<HookName>`` source resolves to :class:`EventType.CLAUDE_HOOK`.
    Anything else raises :class:`UnknownEventSourceError` rather than silently
    defaulting to a commit.
    """
    known = _RESOLVABLE_SOURCES.get(source)
    if known is not None:
        return known
    if source.startswith(_CLAUDE_SOURCE_PREFIX):
        return EventType.CLAUDE_HOOK
    raise UnknownEventSourceError(f"unknown event source {source!r}")


def _is_release_event(record: EventRecord) -> bool:
    """Whether an event should be flagged as a release.

    Multiple task ids on a ``claude:<hook>`` or ``agent`` event just means several
    tracked runs were active when the event fired: ``log-event
    --attach-active-run`` attaches EVERY active run's task id, so a routine
    Read/Bash hook naturally carries ``task_ids=[t1, t2]`` with two tasks tracked.
    Those sources are therefore never releases by task count. The other
    release-bearing sources keep the historical ``len(task_ids) > 1`` heuristic.
    """
    if record.source == "agent" or record.source.startswith(_CLAUDE_SOURCE_PREFIX):
        return False
    return len(record.task_ids) > 1


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
        is_release=_is_release_event(record),
        subject=record.subject,
        pr_title=payload.get("pr_title", ""),
        pr_body=payload.get("pr_body", ""),
    )


def raw_event_to_event_record(event: RawEvent) -> EventRecord:
    """Convert a pure :class:`RawEvent` to a persistable :class:`EventRecord`."""
    return EventRecord(
        id=None,
        source=_EVENT_TYPE_TO_SOURCE[event.event_type],
        timestamp=event.timestamp,
        task_ids=list(event.task_ids),
        repo=event.repo,
        pr_num=event.pr_num,
        branch=event.branch,
        subject=event.subject,
        payload={"pr_title": event.pr_title, "pr_body": event.pr_body},
    )


def is_synthetic_tick(record: EventRecord) -> bool:
    """Return whether ``record`` is a synthetic calendar meeting tick (#370).

    Ticks carry a ``payload.synthetic`` marker so they can be filtered out of the
    gap-sweep's raw-event population without excluding them from session
    derivation (they must still form the meeting's session). The marker lives in
    ``payload`` rather than being inferred from ``source`` so a future non-tick
    ``calendar`` event would not be misclassified.
    """
    payload = record.payload or {}
    return bool(payload.get(_SYNTHETIC_PAYLOAD_KEY))


def load_raw_events(
    state: LocalStateClient,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    *,
    exclude_synthetic: bool = False,
) -> list[RawEvent]:
    """Read stored events (optionally range-bounded) as pure :class:`RawEvent`.

    When ``exclude_synthetic`` is set, synthetic calendar meeting ticks are
    dropped (see :func:`is_synthetic_tick`). The gap-sweep optimizer passes it so
    a meeting-heavy day's tick trains do not dominate the population it scores;
    the derivation read path never excludes them, so meetings still sessionize.
    """
    return [
        event_record_to_raw_event(record)
        for record in state.get_events(start, end)
        if not (exclude_synthetic and is_synthetic_tick(record))
    ]
