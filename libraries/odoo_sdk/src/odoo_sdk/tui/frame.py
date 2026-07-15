"""Pure frame composition for the TUI.

This module assembles the whole screen as data: given the queried sessions, the
computed stats, and the current date window, it lays out the header, the hero
timeline panel, the stats panel, and the footer help line into a single grid of
character rows sized to the terminal. The :mod:`~odoo_sdk.tui.app` driver blits
the returned rows verbatim, so all layout arithmetic (and its reflow on
``KEY_RESIZE``) is tested here without a terminal.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from odoo_sdk.utilities.stats import SessionStats, compute_stats

from .box import ROUNDED, draw_box, place_text
from .meter import meter_row
from .timeline import build_timeline
from .window import DateWindow

Session = Mapping[str, Any]

# Minimum width one panel needs before its content is worth drawing, and the
# minimum full-frame width (both panels side by side) and height.
_MIN_PANEL_WIDTH = 20
_MIN_WIDTH = 2 * _MIN_PANEL_WIDTH
_MIN_HEIGHT = 8

_FOOTER = (
    " ←/→ start  ↑/↓ end  e:export  u:upload  r:resync  t:triage  v:review  q:quit "
)

# Shown under the empty-state hint so a blank window always names a next step.
_EMPTY_GUIDANCE = "log events via start_task / odoo-sdk log-event, or widen the window"


@dataclass(frozen=True)
class Frame:
    """A fully-composed screen: one string per terminal row."""

    rows: list[str]
    width: int
    height: int


def _fit(text: str, width: int) -> str:
    """Return ``text`` clipped or space-padded to exactly ``width`` columns."""
    if width <= 0:
        return ""
    if len(text) > width:
        return text[:width]
    return text + " " * (width - len(text))


def _header(window: DateWindow, stats: SessionStats, width: int) -> str:
    """Return the single-row header summarizing the window and headline counts."""
    span = f"{window.start_iso()} → {window.end_iso()} ({window.days}d)"
    counts = (
        f"{stats.session_count} sessions  {stats.task_count} tasks  "
        f"{stats.total_events} events  {stats.session_hours:.1f}h"
    )
    return _fit(f" odoo-tui  {span}   {counts}", width)


def _stat_lines(stats: SessionStats, inner_width: int) -> list[str]:
    """Return the stats-panel body lines (label + value + optional meter)."""
    lines = [
        f"session hours   {stats.session_hours:>8.2f}",
        f"events/day      {stats.events_per_day:>8.2f}",
        f"events/week     {stats.events_per_week:>8.2f}",
        f"peak parallel   {stats.peak_concurrency:>8d}",
        f"mean parallel   {stats.mean_concurrency:>8.2f}",
        f"overlap ratio   {stats.overlap_ratio:>8.2f}",
    ]
    meter_width = max(0, inner_width - 18)
    lines.append(f"target util   {meter_row(stats.target_utilization, meter_width)}")
    lines.append(f"calendar util {meter_row(stats.calendar_utilization, meter_width)}")
    return [_fit(line, inner_width) for line in lines]


def _fill_panel(
    panel: list[str], content: Sequence[str], *, top: int = 1, left: int = 1
) -> list[str]:
    """Overlay ``content`` rows onto a drawn ``panel`` starting at ``(top, left)``.

    Rows that would fall past the panel's last interior row are dropped, so the
    panel border is never overwritten.
    """
    filled = list(panel)
    for offset, line in enumerate(content):
        row_index = top + offset
        if row_index >= len(panel) - 1:
            break
        filled[row_index] = place_text(filled[row_index], line, left)
    return filled


def _empty_lines(empty_hint: str, inner_width: int) -> list[str]:
    """Return the empty-window body: the diagnostic hint plus a guidance line.

    ``empty_hint`` distinguishes "no data at all" from "data exists but isn't
    derivable in this window"; when absent (e.g. a direct render), the bare
    placeholder stands in. Both lines are truncated to the interior width so they
    never overflow the panel at narrow terminal sizes.
    """
    hint = empty_hint or "(no sessions in window)"
    return [line[:inner_width] for line in (hint, _EMPTY_GUIDANCE)]


def _timeline_lines(
    sessions: Sequence[Session],
    window: DateWindow,
    inner_width: int,
    inner_height: int,
    empty_hint: str = "",
) -> list[str]:
    """Return the hero timeline body: one ``label | bar`` line per lane."""
    from datetime import datetime, time

    # Bind the naive date window to the local timezone so the axis bounds are
    # tz-aware; stored session timestamps carry offsets, and subtracting a naive
    # bound from an aware timestamp raises TypeError (issue #333).
    start = datetime.combine(window.start, time.min).astimezone()
    end = datetime.combine(window.end, time.max).astimezone()
    label_width = min(22, max(8, inner_width // 3))
    bar_width = max(1, inner_width - label_width - 1)
    grid = build_timeline(sessions, start, end, bar_width)
    lines: list[str] = []
    for lane in grid.lanes[:inner_height]:
        label = _fit(lane.label, label_width)
        lines.append(f"{label} {lane.row}")
    if not lines:
        lines.extend(_empty_lines(empty_hint, inner_width))
    return lines


def _compose_panels(
    sessions: Sequence[Session],
    stats: SessionStats,
    window: DateWindow,
    width: int,
    body_height: int,
    empty_hint: str = "",
) -> list[str]:
    """Compose the timeline and stats panels side by side into body rows.

    The stats panel takes a third of the width but is clamped so both panels keep
    at least ``_MIN_PANEL_WIDTH`` columns, so the timeline (the hero view) is
    never starved on a narrow-but-valid terminal.
    """
    stats_width = min(max(_MIN_PANEL_WIDTH, width // 3), width - _MIN_PANEL_WIDTH)
    timeline_width = width - stats_width
    timeline = draw_box(timeline_width, body_height, "timeline", chars=ROUNDED)
    stats_box = draw_box(stats_width, body_height, "stats", chars=ROUNDED)
    timeline = _fill_panel(
        timeline,
        _timeline_lines(
            sessions, window, timeline_width - 2, body_height - 2, empty_hint
        ),
    )
    stats_box = _fill_panel(stats_box, _stat_lines(stats, stats_width - 2))
    return [t + s for t, s in zip(timeline, stats_box)]


def compose_frame(
    sessions: Sequence[Session],
    window: DateWindow,
    width: int,
    height: int,
    *,
    target_hours_per_day: float = 8.0,
    stats: SessionStats | None = None,
    empty_hint: str = "",
) -> Frame:
    """Compose the full screen for ``sessions`` over ``window`` at ``width x height``.

    :param sessions: Session dicts (the ``query_sessions`` shape).
    :param window: The current date window driving the timeline axis and header.
    :param width: Terminal column count.
    :param height: Terminal row count.
    :param target_hours_per_day: Billable target for the utilization meter.
    :param stats: Precomputed stats; computed from ``sessions`` when omitted.
    :param empty_hint: Diagnostic shown in place of the bare placeholder when the
        window derived no sessions; ignored when sessions are present.
    :return: A :class:`Frame` of exactly ``height`` rows each ``width`` wide.
    """
    if width < _MIN_WIDTH or height < _MIN_HEIGHT:
        return Frame(
            rows=[_fit("terminal too small", width)] * max(height, 0),
            width=width,
            height=height,
        )
    resolved = (
        stats
        if stats is not None
        else compute_stats(sessions, target_hours_per_day=target_hours_per_day)
    )
    header = _header(window, resolved, width)
    footer = _fit(_FOOTER, width)
    body_height = height - 2
    body = _compose_panels(
        sessions, resolved, window, width, body_height, empty_hint
    )
    rows = [header] + [_fit(row, width) for row in body] + [footer]
    return Frame(rows=rows[:height], width=width, height=height)
