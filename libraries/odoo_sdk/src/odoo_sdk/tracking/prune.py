"""Event-retention pruning with an un-uploaded-session guard (issue #363).

PreToolUse hooks log one ``events`` row per tool call, so a heavy day leaves
thousands of rows per DB forever; nothing ever deletes them, and export /
diagnostics degrade linearly. This module plans and executes a retention prune of
events strictly older than a horizon, deleting through the sole event-DELETION
primitive (:meth:`odoo_sdk.state.LocalStateClient.delete_events`).

**The guard.** Sessions are derived at query time and identified by
``task|min_event_id`` — a session's key IS the id of its earliest event. That
makes naive pruning unsafe in two ways, and the planner refuses both:

* Deleting *some* of a session's events could remove its minimum-id event and
  shift the session's key. If that session was already uploaded, its
  ``session_uploads`` ledger row (keyed on the old key) would orphan, and the
  upload path's orphan sweep would then zero its legitimately-billed Odoo row.
* Deleting a session that has **not** been uploaded would silently drop tracked
  work that was never billed.

So the contract is **whole-session, all-or-nothing, and only for fully-uploaded,
fully-aged sessions**:

1. Derive every session that started on or before the cutoff.
2. A session is *prunable* only when it is **entirely aged** (its ``ended_at`` is
   strictly older than the cutoff, so *all* its events are) **and** it has a
   ``session_uploads`` ledger record (it is fully uploaded).
3. Every non-prunable session's events are *protected* — kept whole — so no
   surviving session's minimum-id event (its key) can ever shift. This covers
   both un-uploaded sessions (the guard) and sessions that straddle the cutoff.
4. A prunable session is deleted only when it is *fully* prunable: none of its
   events are protected by a non-prunable session (a multi-task event can be
   shared). A prunable-but-shared session is kept whole too, so its key can't
   shift either.
5. Aged events belonging to **no** session (untargeted diagnostic hook events —
   the bulk of the bloat) are always prunable; they can never be a session key.

When a fully-prunable session's events are deleted, its ledger mapping is
retired (:meth:`~odoo_sdk.state.LocalStateClient.delete_session_upload`) so the
now-non-deriving key can never be swept: the Odoo row keeps its billed hours, the
local mapping simply goes away. Prune is therefore a purely **local** operation —
it never touches Odoo.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from odoo_sdk.state import LocalConfig, LocalStateClient, SessionWindow, session_key

# Config key (file ``[behavior]`` section) and environment variable that carry the
# optional auto-prune horizon in whole days. Both are retention-named so they can
# never collide with the connection/behavior keys owned elsewhere. A value of 0
# (or unset/invalid) means auto-prune is OFF: a bare ``prune`` with no
# ``--older-than`` prunes nothing.
PRUNE_HORIZON_CONFIG_KEY = "prune_horizon_days"
PRUNE_HORIZON_ENV_VAR = "ODOO_PRUNE_HORIZON_DAYS"


def _coerce_days(value: Any) -> Optional[int]:
    """Coerce a raw horizon value to a positive whole-day int, else None.

    Values arrive from the config file or the environment as strings (or absent);
    anything that is not a positive integer means "auto-prune off" and yields
    None rather than raising.
    """
    try:
        days = int(value)
    except (TypeError, ValueError):
        return None
    return days if days > 0 else None


def resolve_horizon(config: LocalConfig) -> Optional[int]:
    """Resolve the configured auto-prune horizon in days, or None when off.

    Honors the project's **File > Environment > Default** precedence without
    touching :mod:`odoo_sdk.state.config`: the file ``[behavior] prune_horizon_days``
    value is surfaced by :meth:`LocalConfig.get` (``LocalConfig`` already carries
    unknown file keys through), and :data:`PRUNE_HORIZON_ENV_VAR` is the env
    fallback. Absent/zero/invalid at both layers means auto-prune is disabled.
    """
    file_value = config.get(PRUNE_HORIZON_CONFIG_KEY)
    if file_value is not None:
        return _coerce_days(file_value)
    return _coerce_days(os.environ.get(PRUNE_HORIZON_ENV_VAR))


@dataclass(frozen=True)
class PrunePlan:
    """The decided outcome of a prune, computed before anything is deleted.

    :param cutoff: The horizon instant; only events strictly older are candidates.
    :param delete_ids: Event ids that are safe to delete, ascending.
    :param retire_keys: Session keys whose ledger mapping must be retired because
        their fully-uploaded, fully-aged session is being deleted whole.
    :param kept_session_count: Non-prunable sessions whose events were protected
        (un-uploaded, straddling, or shared) — reported for the summary.
    """

    cutoff: datetime
    delete_ids: list[int] = field(default_factory=list)
    retire_keys: list[str] = field(default_factory=list)
    kept_session_count: int = 0

    @property
    def delete_count(self) -> int:
        """Number of events this plan would delete."""
        return len(self.delete_ids)


def _is_entirely_aged(session: SessionWindow, cutoff: datetime) -> bool:
    """Return True when every event in ``session`` is strictly older than cutoff.

    ``ended_at`` is the session's maximum event timestamp, so ``ended_at < cutoff``
    proves the whole session is aged with a single comparison.
    """
    return session.ended_at < cutoff


def _partition(
    state: LocalStateClient,
    sessions: list[SessionWindow],
    cutoff: datetime,
) -> tuple[list[SessionWindow], list[SessionWindow]]:
    """Split derived sessions into (prunable, non_prunable).

    A session is prunable only when it is entirely aged AND has an upload ledger
    record; everything else (un-uploaded, or straddling the cutoff) is
    non-prunable and will have its events protected.
    """
    prunable: list[SessionWindow] = []
    non_prunable: list[SessionWindow] = []
    for session in sessions:
        uploaded = state.get_session_upload(session_key(session)) is not None
        if _is_entirely_aged(session, cutoff) and uploaded:
            prunable.append(session)
        else:
            non_prunable.append(session)
    return prunable, non_prunable


def plan_prune(
    state: LocalStateClient,
    config: LocalConfig,
    *,
    older_than_days: int,
    now: Optional[datetime] = None,
) -> PrunePlan:
    """Decide exactly which events may be pruned to the given horizon.

    Implements the guard documented in this module: it derives every session that
    started on or before the cutoff, protects every non-prunable session's events
    whole, and deletes only aged events that no protected session claims — i.e.
    the events of fully-uploaded, fully-aged, unshared sessions plus untargeted
    diagnostic events that never form a session. Purely a planner: it reads state
    but deletes nothing.

    :param older_than_days: Prune events strictly older than this many days.
    :param now: Reference instant (defaults to the current UTC time); injectable
        so tests can fix the horizon deterministically.
    :return: The :class:`PrunePlan` for :func:`execute_prune` to carry out.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=older_than_days)
    sessions = state.derive_sessions_overlapping(
        datetime.min, cutoff, gap_secs=config.session_gap_secs
    )
    prunable, non_prunable = _partition(state, sessions, cutoff)

    protected: set[int] = set()
    for session in non_prunable:
        protected.update(session.event_ids)

    retire_keys: list[str] = []
    for session in prunable:
        if protected.isdisjoint(session.event_ids):
            retire_keys.append(session_key(session))
        else:
            # Shared with a non-prunable session: keep the whole session so its
            # minimum-id key cannot shift, and leave its ledger mapping in place.
            protected.update(session.event_ids)

    aged_ids = state.event_ids_before(cutoff)
    delete_ids = [event_id for event_id in aged_ids if event_id not in protected]
    return PrunePlan(
        cutoff=cutoff,
        delete_ids=delete_ids,
        retire_keys=retire_keys,
        kept_session_count=len(non_prunable),
    )


def execute_prune(state: LocalStateClient, plan: PrunePlan) -> dict[str, Any]:
    """Carry out a planned prune: delete events, retire ledger rows, reclaim space.

    Deletes through the sole event-DELETION primitive, retires the ledger mapping
    of every fully-deleted uploaded session (so the orphan sweep never zeroes its
    billed Odoo row), and runs a best-effort ``VACUUM`` when anything was removed.

    :return: A summary dict with ``deleted`` (events removed), ``retired``
        (ledger mappings retired), and ``kept_sessions`` (protected sessions).
    """
    deleted = state.delete_events(plan.delete_ids)
    for key in plan.retire_keys:
        state.delete_session_upload(key)
    if deleted:
        state.vacuum()
    return {
        "deleted": deleted,
        "retired": len(plan.retire_keys),
        "kept_sessions": plan.kept_session_count,
    }
