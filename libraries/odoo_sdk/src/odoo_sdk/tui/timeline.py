"""Pure timeline lane layout for the TUI hero view.

Sessions are grouped into lanes keyed by ``(task_id, repo, strategy_name)`` and
each session is drawn as a filled bar spanning its true start/end mapped onto a
fixed pixel-column width. Because bars keep their real bounds, sessions that run
in parallel across different lanes line up vertically, so concurrent work is
visible at a glance. Optional event ticks mark event timestamps within a lane.

The renderer is pure: it takes session and event dicts (the ``query_sessions``
shape) plus a window ``[start, end]`` and a width, and returns a
:class:`TimelineGrid` of character rows a driver blits — it never touches a
terminal.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from odoo_sdk.state.db import AGENTLESS_REPO_SENTINEL

Session = Mapping[str, Any]

_BAR = "█"
_BAR_EDGE = "▌"
_TICK = "╿"
_EMPTY = " "

# Repo-less agent sessions carry the reserved NUL-prefixed sentinel as their
# ``repo`` (see :data:`AGENTLESS_REPO_SENTINEL`); it must never reach a curses
# ``addstr`` (embedded NUL raises ``ValueError``, #451), so the lane label shows
# this printable stand-in instead — mirroring triage's display-key translation.
_AGENTLESS_LABEL = "(agent)"


@dataclass(frozen=True)
class Lane:
    """One rendered timeline lane: its key label and its bar/tick row string."""

    key: tuple[str, str, str]
    label: str
    row: str
    session_count: int


@dataclass(frozen=True)
class TimelineGrid:
    """The laid-out timeline: one :class:`Lane` per group, plus the axis span."""

    lanes: list[Lane]
    width: int
    start: datetime
    end: datetime

    @property
    def rows(self) -> list[str]:
        """Return just the bar/tick row strings, lane order preserved."""
        return [lane.row for lane in self.lanes]


def _parse(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp string into a tz-aware :class:`datetime`.

    Naive timestamps are coerced to UTC so mixing naive and offset-carrying
    stored timestamps can never raise on subtraction while rendering (#333).
    """
    parsed = datetime.fromisoformat(ts)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _ensure_aware(dt: datetime) -> datetime:
    """Return ``dt`` unchanged if aware, else bound to UTC.

    Parsed session timestamps are always tz-aware (see :func:`_parse`), so the
    axis bounds must be aware too or subtracting them raises TypeError (#333).
    """
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _to_column(ts: datetime, start: datetime, span_secs: float, width: int) -> int:
    """Map a timestamp to a 0-based column, clamped into ``[0, width - 1]``."""
    if span_secs <= 0 or width <= 0:
        return 0
    offset = (ts - start).total_seconds()
    fraction = offset / span_secs
    col = int(fraction * width)
    return 0 if col < 0 else width - 1 if col >= width else col


def _lane_key(session: Session) -> tuple[str, str, str]:
    """Return the ``(task_id, repo, strategy_name)`` lane key for a session."""
    return (
        str(session["task_id"]),
        str(session.get("repo", "")),
        str(session.get("strategy_name", "")),
    )


def _group_sessions(
    sessions: Sequence[Session],
) -> dict[tuple[str, str, str], list[Session]]:
    """Group sessions by lane key, preserving first-seen lane order."""
    groups: dict[tuple[str, str, str], list[Session]] = {}
    for session in sessions:
        groups.setdefault(_lane_key(session), []).append(session)
    return groups


def _paint_bar(cells: list[str], lo: int, hi: int) -> None:
    """Fill ``cells[lo..hi]`` with bar glyphs, marking single-column bars faintly.

    A zero-width bar (``lo == hi``) is drawn as a half-block edge so a very short
    session is still visible without implying a longer span.
    """
    if lo == hi:
        cells[lo] = _BAR_EDGE if cells[lo] == _EMPTY else cells[lo]
        return
    for col in range(lo, hi + 1):
        cells[col] = _BAR


def _paint_ticks(cells: list[str], columns: list[int]) -> None:
    """Overlay event ticks at the given columns where no bar is already drawn."""
    for col in columns:
        if cells[col] == _EMPTY:
            cells[col] = _TICK


def _lane_row(
    lane_sessions: Sequence[Session],
    start: datetime,
    span_secs: float,
    width: int,
    *,
    show_ticks: bool,
) -> str:
    """Render one lane's sessions (and optional event ticks) to a row string."""
    cells = [_EMPTY] * width
    for session in lane_sessions:
        lo = _to_column(_parse(session["started_at"]), start, span_secs, width)
        hi = _to_column(_parse(session["ended_at"]), start, span_secs, width)
        if hi < lo:
            lo, hi = hi, lo
        _paint_bar(cells, lo, hi)
    if show_ticks:
        tick_cols = [
            _to_column(_parse(event["timestamp"]), start, span_secs, width)
            for session in lane_sessions
            for event in session.get("events", []) or []
        ]
        _paint_ticks(cells, tick_cols)
    return "".join(cells)


def _lane_label(key: tuple[str, str, str]) -> str:
    """Return a compact human label for a lane key.

    A repo-less agent session's ``repo`` is the NUL-prefixed sentinel; it is
    translated to a printable stand-in here so the raw sentinel never reaches
    the screen (embedded NUL crashes ``curses.addstr`` with ``ValueError``, #451).
    """
    task_id, repo, strategy = key
    if repo == AGENTLESS_REPO_SENTINEL:
        repo_tail = _AGENTLESS_LABEL
    else:
        repo_tail = repo.rsplit("/", 1)[-1] if repo else ""
    parts = [f"#{task_id}"]
    if repo_tail:
        parts.append(repo_tail)
    if strategy:
        parts.append(strategy)
    return " ".join(parts)


def build_timeline(
    sessions: Sequence[Session],
    start: datetime,
    end: datetime,
    width: int,
    *,
    show_ticks: bool = True,
) -> TimelineGrid:
    """Lay sessions out as one bar-row per lane over the window ``[start, end]``.

    :param sessions: Session dicts (the ``query_sessions`` shape).
    :param start: Left edge of the timeline axis (window start).
    :param end: Right edge of the timeline axis (window end).
    :param width: Number of character columns in each lane row (>= 1).
    :param show_ticks: When True, overlay event ticks from each session's events.
    :return: A :class:`TimelineGrid` with one :class:`Lane` per group.
    """
    if width < 1:
        raise ValueError("build_timeline requires width >= 1")
    start = _ensure_aware(start)
    end = _ensure_aware(end)
    span_secs = (end - start).total_seconds()
    groups = _group_sessions(sessions)
    lanes: list[Lane] = []
    for key, lane_sessions in groups.items():
        row = _lane_row(lane_sessions, start, span_secs, width, show_ticks=show_ticks)
        lanes.append(
            Lane(
                key=key,
                label=_lane_label(key),
                row=row,
                session_count=len(lane_sessions),
            )
        )
    return TimelineGrid(lanes=lanes, width=width, start=start, end=end)
