"""Checkpoint-cadence hint derived from local task-note events (#387).

A lightweight, best-effort signal surfaced in the ``start_task`` and
``task_note`` tool responses: how long it has been since the run's last
recorded ``task_note`` (or since the run started, when none exists yet), plus a
boolean nudge once that gap crosses a threshold. The prompt already asks the
agent to checkpoint on a cadence; this hint gives the tooling an explicit signal
rather than relying on prompt wording alone.

It reads only local event data and degrades silently: any failure — no tracker
DB, a query error — yields an empty hint so the calling tool never breaks,
mirroring how MCP telemetry emission is swallowed in ``mcp/server.py``.
"""

from datetime import datetime, timezone
from typing import Any

# Minutes since the last ``task_note`` beyond which the response nudges the
# agent to post a checkpoint. 15 minutes is long enough not to fire mid
# file-group on a normally-paced implementation, yet short enough to flag the
# multi-file, note-sparse stretch that motivated #387 (task 24648 posted only 2
# notes across ~8 minutes and 8 changed files). It is a fixed constant, not a
# config key, to avoid adding a configuration surface for a pure hint.
CHECKPOINT_THRESHOLD_MINUTES = 15


def _elapsed_minutes(since: datetime) -> int:
    """Return whole minutes elapsed from ``since`` to now (UTC), floored at 0.

    A naive ``since`` is treated as UTC so it can be subtracted from an aware
    ``now``; a negative delta (clock skew) is clamped to 0.
    """
    reference = since if since.tzinfo is not None else since.replace(tzinfo=timezone.utc)
    seconds = (datetime.now(timezone.utc) - reference).total_seconds()
    return max(0, int(seconds // 60))


def checkpoint_hint(
    state: Any, task_id: int, run_started_at: datetime
) -> dict[str, Any]:
    """Return a checkpoint-cadence hint, or ``{}`` when it cannot be computed.

    The elapsed time is measured from the run's most recent recorded
    ``task_note`` event, falling back to ``run_started_at`` when the run has
    posted no note yet.

    :param state: Local state client exposing ``last_note_at(task_id)``.
    :param task_id: The task whose note cadence is measured.
    :param run_started_at: Fallback reference used when the run has no note yet.
    :return: ``{"minutes_since_last_note": int, "suggest_checkpoint": bool}`` on
        success, or ``{}`` when the underlying event store is unavailable so the
        caller can merge it into a response unconditionally without breaking.
    """
    try:
        last = state.last_note_at(task_id)
        reference = last if last is not None else run_started_at
        minutes = _elapsed_minutes(reference)
    except Exception:
        # Best-effort: a missing tracker DB or any query hiccup must never break
        # the tool call that requested the hint.
        return {}
    return {
        "minutes_since_last_note": minutes,
        "suggest_checkpoint": minutes >= CHECKPOINT_THRESHOLD_MINUTES,
    }
