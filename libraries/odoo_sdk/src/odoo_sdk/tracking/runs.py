"""Presentation helpers shared by the run-listing commands.

The ``list_runs`` and ``report_runs`` built-in commands both surface tracker
:class:`~odoo_sdk.state.models.TaskRun` rows as plain, JSON-serializable dicts so
every frontend (CLI table, MCP tool wire schema, TUI) formats the same shape.
Keeping the projection here avoids the two commands importing each other.

**Elapsed time is sessionization-derived (#506).** ``TaskRun.elapsed_seconds`` is
raw start-to-stop wall clock on a single FSM row: it knows nothing about gaps and
nothing about work that happened between two run rows of the same effort, so it
routinely disagrees with the hours that actually bill. The billing hours come from
sessionization, so the run tables read from sessionization too — when a state
client and config are supplied, ``elapsed`` is the duration of the derived session
windows overlapping the run, exactly the windows
:func:`~odoo_sdk.billing.upload.upload_sessions` bills from.

A run that spans an effort shared with an adjacent run row reports that effort's
*whole* sessionized duration on both rows (an effort split across rows has no
single wall clock to attribute), so the rows are not additive. ``session_keys``
names the contributing windows, which makes the sharing visible rather than
silent, and ``elapsed_source`` says which clock produced the number.

Without a state client the projection degrades to wall clock and labels it
``elapsed_source: "wall_clock"``, so a run table is never mistakable for an hours
report.
"""

from datetime import datetime, timezone
from typing import Any, Optional

from odoo_sdk.state import LocalConfig, LocalStateClient, TaskRun, session_key


def format_elapsed(seconds: float) -> str:
    """Render a duration as the ``0h 0m 0s`` string the run tables display.

    :param seconds: Duration in seconds; truncated to whole seconds.
    :type seconds: float
    :returns: The duration as ``"<h>h <m>m <s>s"``.
    :rtype: str
    """
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}h {minutes}m {secs}s"


def sessionized_elapsed(
    run: TaskRun, state: LocalStateClient, config: LocalConfig
) -> tuple[Optional[float], tuple[str, ...]]:
    """Derive the run's elapsed time from sessionization rather than wall clock.

    Sessions are derived with the same call and the same
    ``session_gap_secs`` the billing path uses
    (:meth:`~odoo_sdk.state.LocalStateClient.derive_sessions_overlapping`), scoped
    to this run's task and to the run's own ``[started_at, stopped_at]`` bounds. An
    active run is bounded at "now". Windows are returned whole (never clipped to
    the run), which is the point: a session bridging a stopped gap between two run
    rows is one effort and reports as one duration.

    :param run: The tracker run to measure.
    :type run: TaskRun
    :param state: Local state client the sessions derive from.
    :type state: LocalStateClient
    :param config: Resolved settings supplying ``session_gap_secs``.
    :type config: LocalConfig
    :returns: The summed window duration in seconds and the contributing session
        keys, or ``(None, ())`` when the run has no derived sessions at all (no
        events were ever recorded against it).
    :rtype: tuple[Optional[float], tuple[str, ...]]
    """
    end = run.stopped_at or datetime.now(timezone.utc)
    windows = state.derive_sessions_overlapping(
        run.started_at,
        end,
        gap_secs=config.session_gap_secs,
        task_id=str(run.task_id),
    )
    if not windows:
        return None, ()
    return (
        sum(window.duration_seconds for window in windows),
        tuple(session_key(window) for window in windows),
    )


def run_summary(
    run: TaskRun,
    state: Optional[LocalStateClient] = None,
    config: Optional[LocalConfig] = None,
) -> dict[str, Any]:
    """Project one :class:`TaskRun` onto the fields the run tables display.

    :param run: The tracker run to summarize.
    :type run: TaskRun
    :param state: Local state client to derive sessions from. Omit to fall back
        to wall clock.
    :type state: Optional[LocalStateClient]
    :param config: Resolved settings supplying ``session_gap_secs``. Omit to fall
        back to wall clock.
    :type config: Optional[LocalConfig]
    :returns: A dict with the run's SQLite ``id``, Odoo ``task_id``, task and
        project names, FSM ``state`` value, human-readable ``elapsed`` time and
        its ``elapsed_seconds``, the ``elapsed_source`` that produced them
        (``"sessionization"`` or ``"wall_clock"``), the raw
        ``elapsed_wall_clock`` for reference, and the ``session_keys`` that
        contributed to a sessionized elapsed.
    :rtype: dict[str, Any]
    """
    wall_clock = run.elapsed_seconds
    seconds, keys, source = wall_clock, (), "wall_clock"
    if state is not None and config is not None:
        derived, derived_keys = sessionized_elapsed(run, state, config)
        if derived is not None:
            seconds, keys, source = derived, derived_keys, "sessionization"
    return {
        "id": run.id,
        "task_id": run.task_id,
        "task_name": run.task_name,
        "project_name": run.project_name,
        "state": run.state.value,
        "elapsed": format_elapsed(seconds),
        "elapsed_seconds": round(seconds, 2),
        "elapsed_source": source,
        "elapsed_wall_clock": format_elapsed(wall_clock),
        "session_keys": list(keys),
    }
