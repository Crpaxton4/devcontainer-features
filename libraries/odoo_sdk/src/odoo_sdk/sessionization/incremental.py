"""Pure incremental sessionizer for one ``(task, repo, strategy)`` group.

Global sessionization detects sessions over the *whole* event stream per group:
a session is a maximal run of events where every consecutive pair is separated by
no more than a fixed ``gap``. This module maintains that detection incrementally.
Given the existing sessions (and their linked events) for a single group plus a
batch of new events, it recomputes the group's partition over the union of events
and diffs it against the existing sessions, emitting:

* the updated set of sessions (ids preserved wherever a run inherits an existing
  session, so identity is stable), and
* per-event link deltas describing which session each event now belongs to.

It implements extend / merge / split / no-op-relink / create against the existing
sessions. It is *pure*: data in, data out. No SQLite, no clock, no I/O. Because it
operates on one group's local neighborhood (passed in by the adapter), its cost is
bounded by ``new events + affected sessions`` and is independent of total history.

The result is identical to a from-scratch rebuild of the group: partitioning is a
function of the event multiset and the gap alone, so ``incremental ≡ full rebuild``.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime

# Repo-less agent events cannot key on a real repository, so they are grouped
# under this reserved sentinel. It is a valid, stable group key (never a real
# ``owner/repo``) so such events still sessionize deterministically.
AGENTLESS_REPO_SENTINEL = "\x00agent"


@dataclass(frozen=True)
class SessionEvent:
    """One event participating in a group's sessionization, keyed by ``id``.

    ``id`` is the stable persistent identifier (the ``events`` row id) used to
    express link deltas; ``timestamp`` drives partitioning. The descriptive
    fields seed a newly-created session's metadata.
    """

    id: int
    timestamp: datetime
    task_id: str
    repo: str
    strategy_name: str = "development"
    category: str = "Development"
    pr_num: int = 0


@dataclass(frozen=True)
class SessionState:
    """A detected session over one group, with its linked event ids.

    ``id`` is ``None`` for a session created by this run and not yet persisted;
    the adapter assigns a real id on write. ``event_ids`` are the ids of the
    events linked to this session, in ascending timestamp order.
    """

    id: int | None
    task_id: str
    repo: str
    started_at: datetime
    ended_at: datetime
    strategy_name: str
    category: str
    pr_num: int
    event_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class LinkDelta:
    """A single event → session assignment produced by an ingest.

    ``session_ref`` is the id of a persisted session, or ``None`` when the event
    joins a session created by this run (identified positionally in
    :attr:`IncrementalResult.created`). The adapter resolves ``None`` refs to the
    new rows' assigned ids.
    """

    event_id: int
    session_ref: int | None
    new_session_index: int | None = None


@dataclass
class IncrementalResult:
    """The outcome of one incremental ingest for a single group.

    :param sessions: The full, post-ingest session set for the group, in
        started_at order. Existing ids are preserved where a run inherits an
        existing session; created sessions have ``id is None``.
    :param created: The subset of ``sessions`` newly created by this run, in the
        same relative order; ``LinkDelta.new_session_index`` indexes into this.
    :param deleted_ids: Ids of existing sessions this run removed (split/merge
        fallout) and whose rows the adapter should delete.
    :param links: One :class:`LinkDelta` per non-excluded event in the group.
    """

    sessions: list[SessionState]
    created: list[SessionState] = field(default_factory=list)
    deleted_ids: list[int] = field(default_factory=list)
    links: list[LinkDelta] = field(default_factory=list)


def _dedup_sorted_events(events: list[SessionEvent]) -> list[SessionEvent]:
    """Return the events deduped by id and ordered by ``(timestamp, id)``.

    A later occurrence of an id wins, so a relinked/moved event supplied fresh
    alongside an existing session's stale copy partitions at its current
    timestamp.
    """
    seen: dict[int, SessionEvent] = {}
    for event in events:
        seen[event.id] = event
    return sorted(seen.values(), key=lambda event: (event.timestamp, event.id))


def _partition_runs(
    events: list[SessionEvent], gap_secs: int
) -> list[list[SessionEvent]]:
    """Split time-ordered events into runs separated by a gap > ``gap_secs``.

    Each returned run is a session: consecutive events within a run are at most
    ``gap_secs`` apart, and the boundary between runs always exceeds it.
    """
    if not events:
        return []
    runs: list[list[SessionEvent]] = [[events[0]]]
    for event in events[1:]:
        delta = (event.timestamp - runs[-1][-1].timestamp).total_seconds()
        if delta > gap_secs:
            runs.append([event])
        else:
            runs[-1].append(event)
    return runs


def _event_to_session(existing: list[SessionState]) -> dict[int, int]:
    """Map each linked event id to the row id of its owning existing session."""
    owner: dict[int, int] = {}
    for session in existing:
        if session.id is None:
            continue
        for event_id in session.event_ids:
            owner[event_id] = session.id
    return owner


def _claim_existing_id(
    run: list[SessionEvent],
    owner: dict[int, int],
    used: set[int],
) -> int | None:
    """Return an existing session id this run should inherit, or None.

    A run inherits the id of the existing session owning its earliest still-live
    event, giving stable identity across extend/no-op ingests. Each existing id
    is claimed at most once (``used``); a split's later runs therefore become new
    sessions rather than colliding on one id.
    """
    for event in run:
        candidate = owner.get(event.id)
        if candidate is not None and candidate not in used:
            used.add(candidate)
            return candidate
    return None


def _session_from_run(
    run: list[SessionEvent], session_id: int | None
) -> SessionState:
    """Build a :class:`SessionState` spanning one run's events."""
    head = run[0]
    return SessionState(
        id=session_id,
        task_id=head.task_id,
        repo=head.repo,
        started_at=run[0].timestamp,
        ended_at=run[-1].timestamp,
        strategy_name=head.strategy_name,
        category=head.category,
        pr_num=head.pr_num,
        event_ids=tuple(event.id for event in run),
    )


def _links_for_run(
    run: list[SessionEvent], session_id: int | None, new_index: int | None
) -> list[LinkDelta]:
    """Return one link delta per event in a run, pointing at its session."""
    return [
        LinkDelta(
            event_id=event.id,
            session_ref=session_id,
            new_session_index=new_index,
        )
        for event in run
    ]


def _assign_runs(
    runs: list[list[SessionEvent]],
    owner: dict[int, int],
) -> tuple[list[SessionState], list[SessionState], list[LinkDelta]]:
    """Assign session ids to runs, reusing existing ids where a run inherits one."""
    used: set[int] = set()
    sessions: list[SessionState] = []
    created: list[SessionState] = []
    links: list[LinkDelta] = []
    for run in runs:
        session_id = _claim_existing_id(run, owner, used)
        session = _session_from_run(run, session_id)
        sessions.append(session)
        new_index = None
        if session_id is None:
            new_index = len(created)
            created.append(session)
        links.extend(_links_for_run(run, session_id, new_index))
    return sessions, created, links


def sessionize_group(
    existing: list[SessionState],
    events: list[SessionEvent],
    gap_secs: int,
) -> IncrementalResult:
    """Re-sessionize one group's neighborhood and return the session/link deltas.

    :param existing: The group's current sessions with their linked event ids.
        These supply identity (row ids) only; their events must also appear in
        ``events`` for the partition to see them.
    :param events: The complete event set for the affected neighborhood — every
        event linked to an ``existing`` session *plus* the newly ingested events.
        Deduped by id (a fresh copy of an id wins), so a relink/move is honored.
    :param gap_secs: The fixed inactivity gap. Runs separated by more than this
        are distinct sessions. Constant across ingests so identity is stable.
    :return: The full post-ingest session set, the created subset, the ids of
        removed sessions, and one link delta per event.
    :rtype: IncrementalResult
    """
    ordered = _dedup_sorted_events(events)
    runs = _partition_runs(ordered, gap_secs)
    owner = _event_to_session(existing)
    sessions, created, links = _assign_runs(runs, owner)
    kept = {session.id for session in sessions if session.id is not None}
    deleted_ids = [
        session.id
        for session in existing
        if session.id is not None and session.id not in kept
    ]
    return IncrementalResult(
        sessions=sessions,
        created=created,
        deleted_ids=deleted_ids,
        links=links,
    )


def rebuild_group(
    events: list[SessionEvent], gap_secs: int
) -> IncrementalResult:
    """Sessionize a group from scratch (no existing sessions).

    A convenience wrapper equivalent to :func:`sessionize_group` with no existing
    sessions; used to assert ``incremental ≡ full rebuild`` and by callers doing
    a full materialization. Every event becomes a fresh link (``session_ref`` is
    ``None``) into a created session.
    """
    return sessionize_group([], events, gap_secs)


def resolve_repo(repo: str) -> str:
    """Return the group repo key, substituting the sentinel for repo-less events."""
    return repo if repo else AGENTLESS_REPO_SENTINEL


def group_key(event: SessionEvent) -> tuple[str, str, str]:
    """Return the ``(task_id, repo, strategy_name)`` key an event groups under."""
    return (event.task_id, resolve_repo(event.repo), event.strategy_name)


def with_resolved_repo(event: SessionEvent) -> SessionEvent:
    """Return a copy of ``event`` with a repo-less repo replaced by the sentinel."""
    resolved = resolve_repo(event.repo)
    return event if resolved == event.repo else replace(event, repo=resolved)
