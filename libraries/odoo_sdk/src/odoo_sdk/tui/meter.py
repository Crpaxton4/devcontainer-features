"""Pure gradient meter for the TUI.

A meter is a horizontal bar that fills from the left in proportion to a ratio and
carries a per-cell color that sweeps a low->high gradient (e.g. green->red for a
utilization gauge). ``gradient_cells`` returns ``(char, rgb)`` pairs — pure data
a driver colors and blits — while ``meter_row`` returns a plain filled/empty bar
string for the no-color path. No terminal state is touched here.
"""

from __future__ import annotations

_FILLED = "█"
_EMPTY = "░"


def _clamp01(value: float) -> float:
    """Clamp a value into the ``[0.0, 1.0]`` range."""
    return 0.0 if value < 0 else 1.0 if value > 1 else value


def _lerp_channel(low: int, high: int, t: float) -> int:
    """Linearly interpolate one 0..255 channel at position ``t`` in ``[0, 1]``."""
    return round(low + (high - low) * t)


def lerp_color(
    low: tuple[int, int, int],
    high: tuple[int, int, int],
    t: float,
) -> tuple[int, int, int]:
    """Return the RGB triple ``t`` of the way from ``low`` to ``high``."""
    t = _clamp01(t)
    return tuple(_lerp_channel(low[i], high[i], t) for i in range(3))


def meter_row(ratio: float, width: int) -> str:
    """Return a ``width``-wide bar filled left-to-right by ``ratio`` in ``[0, 1]``.

    A ratio at or above 1 fills the whole bar; at or below 0 the bar is empty.
    """
    if width <= 0:
        return ""
    filled = round(_clamp01(ratio) * width)
    return _FILLED * filled + _EMPTY * (width - filled)


def gradient_cells(
    ratio: float,
    width: int,
    *,
    low: tuple[int, int, int] = (0, 200, 0),
    high: tuple[int, int, int] = (220, 0, 0),
) -> list[tuple[str, tuple[int, int, int]]]:
    """Return ``(char, rgb)`` cells for a gradient meter of ``width`` columns.

    Filled cells (the leftmost ``round(ratio * width)``) carry the gradient color
    for their position; empty cells carry the ``low`` anchor color so the bar
    reads as a dimmed track. ``ratio`` is clamped to ``[0, 1]``.
    """
    if width <= 0:
        return []
    filled = round(_clamp01(ratio) * width)
    cells: list[tuple[str, tuple[int, int, int]]] = []
    for index in range(width):
        position = index / (width - 1) if width > 1 else 0.0
        color = lerp_color(low, high, position)
        if index < filled:
            cells.append((_FILLED, color))
        else:
            cells.append((_EMPTY, low))
    return cells
