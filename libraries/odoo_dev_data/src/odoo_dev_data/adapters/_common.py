"""Helpers shared by the git, GitHub and Odoo-chatter pullers.

Task-id extraction, ISO parsing, the resync window, the subprocess runner and
the validate-then-store step used by two or three pullers live here once. The
module is private: the public surface is :mod:`odoo_dev_data.adapters`.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Optional

from odoo_dev_data.config import BEHAVIOR_DEFAULTS, LocalConfig
from odoo_dev_data.events import EventRecord, EventStore

# Payload key flagging extracted task ids that FAILED the ``project.task``
# existence check at resync time (issue #378 item 1). Such ids are kept OUT of
# the event's ``task_ids`` (no phantom lane) but recorded here so the TUI triage
# surface can present the event as a WEAK candidate; a non-empty list means weak.
_UNVALIDATED_TASK_IDS_KEY = "unvalidated_task_ids"

# Minimum task-id magnitude. Real Odoo task ids are 4-5 digits; requiring at
# least this many digits kills false positives where a short client-side number
# (``#31 - Hardcode...``) or a PR cross-reference (``(#189)``) minted a phantom
# task lane (issue #378 item 1).
_MIN_TASK_ID_DIGITS = 4

# Task-id extractors applied to a commit/PR subject and its branch/ref context.
# Documented, ordered forms (all require >= ``_MIN_TASK_ID_DIGITS`` digits):
#   ``#<id>``          GitHub-style reference
#   ``odoo-<id>``      branch convention (case-insensitive)
#   ``[<id>]``         bracketed
#   ``<id>-slug``      branch-prefix convention used on client branches (#622:
#                      id BEFORE the ``-``); anchored to a token start so a
#                      digit run buried mid-token never reads as an id
#   ``task <id>``      PR-title form ``(task NNNNN)`` (optional space/hyphen)
#   ``(<id>)``         trailing ``(NNNNN)`` in a PR title (NOT ``(#NNNNN)``)
#   ``<id> title``     bare leading id at subject START (space/#/:/- after the
#                      digits, issue #654), GATED behind ``allow_leading_id``
#                      (git/GitHub call sites only). ``^`` is safe: the scanned
#                      text is ``f"{subject} {branch}"``, so a branch never
#                      sits at string start, and the join space delimits a
#                      bare-only title; a leading ``NNNNN-`` subject also
#                      matches the ``<id>-slug`` form — dedupe absorbs it
_TASK_ID_PATTERNS = (
    re.compile(rf"#(\d{{{_MIN_TASK_ID_DIGITS},}})"),
    re.compile(rf"odoo-(\d{{{_MIN_TASK_ID_DIGITS},}})", re.IGNORECASE),
    re.compile(rf"\[(\d{{{_MIN_TASK_ID_DIGITS},}})\]"),
    re.compile(rf"(?:^|[\s,/])(\d{{{_MIN_TASK_ID_DIGITS},}})-"),
    re.compile(rf"\btask[ -]?(\d{{{_MIN_TASK_ID_DIGITS},}})\b", re.IGNORECASE),
    re.compile(rf"\((\d{{{_MIN_TASK_ID_DIGITS},}})\)"),
)

# The gated ``<id> title`` form above. Kept OUT of ``_TASK_ID_PATTERNS`` so
# explicit-marker call sites can never grow it by accident; :func:`_extract_task_ids`
# appends it LAST when ``allow_leading_id`` is set, preserving first-seen dedupe
# precedence.
_LEADING_TASK_ID_PATTERN = re.compile(rf"^(\d{{{_MIN_TASK_ID_DIGITS},}})[\s#:-]")


def _extract_task_ids(
    subject: str, branch: str, *, allow_leading_id: bool = False
) -> list[str]:
    """Return the distinct task ids in ``"{subject} {branch}"``, first-seen order.

    Every form requires at least :data:`_MIN_TASK_ID_DIGITS` digits, so a short
    client-side number or a PR cross-reference is never mistaken for a task id.
    ``allow_leading_id`` additionally admits the bare-leading-id form
    (:data:`_LEADING_TASK_ID_PATTERN`, issue #654); only the git/GitHub call
    sites pass True.
    """
    text = f"{subject} {branch}"
    patterns = _TASK_ID_PATTERNS
    if allow_leading_id:
        patterns += (_LEADING_TASK_ID_PATTERN,)
    ids: list[str] = []
    for pattern in patterns:
        for match in pattern.findall(text):
            if match not in ids:
                ids.append(match)
    return ids


def _parse_iso_utc(value: str) -> datetime:
    """Parse an offset-aware ISO-8601 timestamp (git ``%aI`` / GitHub) as UTC."""
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class _Window:
    """The resolved half-open ``[since, until)`` window for one resync.

    Slim on purpose (#652): an account-wide capture derives every event's repo
    from its own PR's ``repository.nameWithOwner``, not from a per-resync
    context.
    """

    since: datetime
    until: datetime


def _window_start(config: Optional[LocalConfig], now: Optional[datetime]) -> datetime:
    """Return the inclusive lower bound ``now - resync_window_days`` in UTC (#378).

    ``now`` defaults to the current UTC time and is injectable for deterministic
    tests.
    """
    days = (
        config.resync_window_days if config else BEHAVIOR_DEFAULTS["resync_window_days"]
    )
    moment = now or datetime.now(timezone.utc)
    return moment - timedelta(days=days)


def _window_bounds(
    config: Optional[LocalConfig],
    now: Optional[datetime],
    start: Optional[date],
    end: Optional[date],
) -> tuple[datetime, datetime]:
    """Resolve one resync run's half-open ``[since, until)`` capture window.

    An explicit inclusive ``start``/``end`` date overrides the rolling default,
    matching the upload command's inclusive-date math: ``since`` is UTC
    midnight of ``start`` and ``until`` is UTC midnight of the day AFTER
    ``end`` (so the whole end day is covered). Without them, ``since`` falls
    back to :func:`_window_start` and ``until`` to ``now``.

    :raises ValueError: When the resolved window is empty or inverted (e.g. an
        ``end`` older than the rolling window's start, or ``start`` after
        ``end``) — the range must fail loudly rather than silently sweeping
        nothing.
    """
    since = (
        datetime.combine(start, time.min, tzinfo=timezone.utc)
        if start
        else _window_start(config, now)
    )
    until = (
        datetime.combine(end + timedelta(days=1), time.min, tzinfo=timezone.utc)
        if end
        else (now or datetime.now(timezone.utc))
    )
    if since >= until:
        raise ValueError(
            f"empty resync window: since {since.isoformat()} is not before "
            f"until {until.isoformat()} (an end-only range must lie inside the "
            "rolling window; pass --start to widen it)"
        )
    return since, until


def _run_capture(cmd: list[str]) -> Optional[str]:
    """Run ``cmd`` and return its stripped stdout, or None when it is unusable.

    A missing binary (``FileNotFoundError``) and a non-zero exit
    (``CalledProcessError`` — not a repo, unauthenticated, no match) both collapse
    to ``None`` so callers can report a skip reason instead of raising.
    """
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip()


def _validate_task_ids(client: Any, ids: set[str]) -> Optional[set[str]]:
    """Return the subset of ``ids`` that name a real ``project.task`` (issue #378).

    One batched ``search_read`` over the whole id set. Returns ``None`` when
    validation cannot run — no client, or Odoo unreachable — so the caller trusts
    the extracted ids as-is (best-effort offline); returns ``set()`` when the
    client is present but ``ids`` is empty. Every id is a digit run by
    construction (all come from :func:`_extract_task_ids`).

    ``client`` is any object with ``execute(model, method, *args, **kwargs)``.
    This package cannot import the client's own error type, so any failure of
    that one call counts as "Odoo unreachable".
    """
    if client is None:
        return None
    assert all(i.isdigit() for i in ids)
    numeric = sorted({int(i) for i in ids})
    if not numeric:
        return set()
    try:
        rows = client.execute(
            "project.task", "search_read", [("id", "in", numeric)], fields=["id"]
        )
    except Exception:  # noqa: BLE001 - the data package cannot name OdooError
        return None
    return {str(row["id"]) for row in rows}


def _finalize_task_attribution(event: EventRecord, valid: Optional[set[str]]) -> None:
    """Move an event's unvalidated ids out of ``task_ids`` into the weak flag.

    Mutates ``event`` in place: validated ids stay in ``task_ids`` (they bill
    normally); unvalidated ids are dropped (so the event never joins a session)
    and recorded under :data:`_UNVALIDATED_TASK_IDS_KEY`. ``valid=None`` means
    validation did not run, so every id is trusted as-is.
    """
    if valid is None:
        return
    known = [i for i in event.task_ids if i in valid]
    unknown = [i for i in event.task_ids if i not in valid]
    event.task_ids = known
    if unknown:
        payload = dict(event.payload) if event.payload else {}
        payload[_UNVALIDATED_TASK_IDS_KEY] = unknown
        event.payload = payload


def _store_pending(
    state: EventStore, pending: list[EventRecord], client: Any
) -> list[EventRecord]:
    """Validate every pending event's ids in ONE batch, then store them deduped.

    Returns the NEWLY inserted events (dedup skips are omitted) so callers can
    both count insertions and report on first-sight rows only — e.g. the
    unattributed-review warning must not re-fire for an already-stored (and
    possibly since-triaged) review on every subsequent resync.
    """
    all_ids = {i for event in pending for i in event.task_ids}
    valid = _validate_task_ids(client, all_ids)
    inserted: list[EventRecord] = []
    for event in pending:
        _finalize_task_attribution(event, valid)
        if state.add_event_dedup(event):
            inserted.append(event)
    return inserted
