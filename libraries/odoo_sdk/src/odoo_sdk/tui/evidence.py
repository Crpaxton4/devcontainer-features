"""Confidence, citations, and overlap for the review surface (#378 items 7-9).

Everything here **informs** the human reviewer and nothing else: it computes,
per derived session, the already-logged badge (item 7), the cross-task
wall-clock overlaps (item 8), and a strong/weak confidence class with a citation
trail extracted from the member events (item 9). None of it auto-trims hours or
auto-uploads — upload stays a manual TUI decision; these are read-only badges the
reviewer weighs.

All functions are pure and tested without a terminal or a live Odoo. The impure
edges (fetching the member events off the store, the best-effort Odoo line read)
live in :mod:`~odoo_sdk.tui.app`; this module only shapes already-fetched data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence

from odoo_sdk.state import EventRecord

STRONG = "STRONG"
WEAK = "WEAK"

# External-id prefixes the resync pullers mint, mapped to a human citation. The
# id tail (SHA / PR number / message id) is extracted so the reviewer sees the
# actual cited artifact rather than an opaque key.
_GIT_RE = re.compile(r"^git:(?P<sha>[0-9a-fA-F]+)$")
_CITATIONS = (
    (re.compile(r"^gh:pr:(?P<n>\d+)$"), "PR #{n}"),
    (re.compile(r"^gh:review:(?P<n>.+)$"), "review {n}"),
    (re.compile(r"^gh:comment:(?P<n>.+)$"), "comment {n}"),
    (re.compile(r"^odoo:mail:(?P<n>.+)$"), "chatter msg {n}"),
)

# Payload keys the #378a sibling may stamp when a task id was extracted but did
# not validate against ``project.task``. Read defensively: a truthy value under
# any of these marks the session weak; their absence is simply no signal (an
# un-flagged event is not evidence of validity either way).
_UNVALIDATED_KEYS = ("unvalidated_task_ids", "task_ids_unvalidated", "unvalidated")


@dataclass(frozen=True)
class Overlap:
    """One cross-task wall-clock overlap: another task and the minutes shared."""

    task_id: str
    minutes: int


@dataclass(frozen=True)
class ReviewCard:
    """A derived session decorated with everything the reviewer needs to judge it.

    :param session_id: The derived session's id (stable min-event id).
    :param task_id: The session's task id (string; may be non-numeric/unbillable).
    :param started_at: ISO start timestamp.
    :param ended_at: ISO end timestamp.
    :param hours: The session's own derived wall-clock hours.
    :param confidence: :data:`STRONG` or :data:`WEAK`.
    :param logged_hours: Hours already logged by hand on this task/day (0.0 when
        none or when Odoo could not be read).
    :param logged_flag: ``""`` (nothing logged), ``"partial"``, or ``"full"``.
    :param overlaps: Cross-task wall-clock overlaps against other sessions.
    :param citations: One citation string per member event, in order.
    :param unvalidated: True when a member event flags an unvalidated task id.
    """

    session_id: int
    task_id: str
    started_at: str
    ended_at: str
    hours: float
    confidence: str
    logged_hours: float
    logged_flag: str
    overlaps: tuple[Overlap, ...]
    citations: tuple[str, ...]
    unvalidated: bool


def _parse_ts(value: str) -> datetime:
    """Parse an ISO timestamp the session render produced."""
    return datetime.fromisoformat(value)


def overlap_seconds(
    a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime
) -> float:
    """Return the seconds two ``[start, end)`` spans share (0.0 when disjoint)."""
    latest_start = max(a_start, b_start)
    earliest_end = min(a_end, b_end)
    return max(0.0, (earliest_end - latest_start).total_seconds())


def compute_overlaps(sessions: Sequence[Mapping[str, Any]]) -> dict[int, tuple[Overlap, ...]]:
    """Return each session's cross-task wall-clock overlaps, keyed by session id.

    Partition-by-task (#352) still bills interleaved multitasking on two different
    tasks twice; this surfaces it so the reviewer trims consciously. Overlaps are
    computed pairwise; same-task pairs are skipped (one task never double-bills
    itself), and only overlaps of at least a minute are recorded (rounded to whole
    minutes), so a sub-minute brush never badges a meaningless "by 0m".
    """
    parsed = [
        (
            session["session_id"],
            str(session["task_id"]),
            _parse_ts(session["started_at"]),
            _parse_ts(session["ended_at"]),
        )
        for session in sessions
    ]
    result: dict[int, tuple[Overlap, ...]] = {}
    for sid_a, task_a, a_start, a_end in parsed:
        overlaps = [
            Overlap(task_b, round(secs / 60))
            for sid_b, task_b, b_start, b_end in parsed
            if sid_b != sid_a
            and task_b != task_a
            and (secs := overlap_seconds(a_start, a_end, b_start, b_end)) >= 60
        ]
        if overlaps:
            result[sid_a] = tuple(overlaps)
    return result


def event_citation(source: str, external_id: str | None) -> str:
    """Return a human citation for one member event.

    Extracts the cited artifact from the resync external id — a short commit SHA,
    a PR number, a review/comment id, or a chatter message id — falling back to
    the raw external id, then the bare source, for events with no external origin.
    """
    if external_id:
        git = _GIT_RE.match(external_id)
        if git:
            return f"commit {git.group('sha')[:7]}"
        for pattern, template in _CITATIONS:
            match = pattern.match(external_id)
            if match:
                return template.format(n=match.group("n"))
        return external_id
    return source


def event_unvalidated(payload: Any) -> bool:
    """Return True when an event payload flags an unvalidated task id (#378a).

    Read defensively: the sibling stamping this flag lands the same wave, so any
    of a few plausible key spellings is honoured, a non-dict/absent payload is no
    signal, and only a truthy value (a non-empty list or ``True``) marks it.
    """
    if not isinstance(payload, dict):
        return False
    return any(payload.get(key) for key in _UNVALIDATED_KEYS)


def _valid_task_id(task_id: Any) -> bool:
    """Return True when ``task_id`` is a positive integer (a billable Odoo task)."""
    text = str(task_id)
    return text.isdigit() and int(text) > 0


def classify_confidence(
    task_id: Any, event_count: int, has_overlap: bool, unvalidated: bool
) -> str:
    """Classify a session :data:`STRONG` or :data:`WEAK` from its evidence.

    Weak when any single signal undermines it: a lone event (nothing corroborates
    the span), a wall-clock overlap (its hours may be double-billed), an
    unvalidated/flagged task id, or a task id that is not a billable Odoo id.
    Strong is the residue: a validated task id backed by multiple direct events
    with no overlap. This only labels the card; it never trims or drops anything.
    """
    if event_count <= 1 or has_overlap or unvalidated or not _valid_task_id(task_id):
        return WEAK
    return STRONG


def logged_flag(session_hours: float, logged_hours: float) -> str:
    """Classify how far already-logged hours cover a session's derived hours.

    ``"full"`` when the day already booked at least the session's own hours on
    this task (the session is almost certainly a duplicate of hand-logged time),
    ``"partial"`` when some but not all is booked, and ``""`` when nothing is.
    """
    if logged_hours <= 0:
        return ""
    if logged_hours + 1e-9 >= session_hours:
        return "full"
    return "partial"


def build_review_cards(
    sessions: Sequence[Mapping[str, Any]],
    events_by_session: Mapping[int, Sequence[EventRecord]],
    logged_by_task_day: Mapping[tuple[str, str], float],
    overlaps_by_id: Mapping[int, tuple[Overlap, ...]],
) -> list[ReviewCard]:
    """Assemble one :class:`ReviewCard` per session from already-fetched inputs.

    Pure: the member events, the already-logged map, and the overlap map are all
    fetched by the caller (the store read and the best-effort Odoo read live in
    the driver), so this is deterministic and terminal-free. Missing member events
    or an absent logged map (offline) degrade to an empty evidence trail and no
    logged badge — the card still classifies and renders.
    """
    cards: list[ReviewCard] = []
    for session in sessions:
        sid = session["session_id"]
        events = list(events_by_session.get(sid, ()))
        overlaps = overlaps_by_id.get(sid, ())
        unvalidated = any(event_unvalidated(event.payload) for event in events)
        confidence = classify_confidence(
            session["task_id"], len(events), bool(overlaps), unvalidated
        )
        hours = float(session["duration_secs"]) / 3600
        day = str(session["started_at"])[:10]
        logged = logged_by_task_day.get((str(session["task_id"]), day), 0.0)
        cards.append(
            ReviewCard(
                session_id=sid,
                task_id=str(session["task_id"]),
                started_at=session["started_at"],
                ended_at=session["ended_at"],
                hours=hours,
                confidence=confidence,
                logged_hours=logged,
                logged_flag=logged_flag(hours, logged),
                overlaps=overlaps,
                citations=tuple(
                    event_citation(event.source, event.external_id) for event in events
                ),
                unvalidated=unvalidated,
            )
        )
    return cards
