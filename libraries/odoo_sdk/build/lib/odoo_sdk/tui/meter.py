"""Pure meter bar for the TUI.

A meter is a horizontal bar that fills from the left in proportion to a ratio.
``meter_row`` returns a plain filled/empty bar string. No terminal state is
touched here.
"""

from __future__ import annotations

_FILLED = "█"
_EMPTY = "░"


def _clamp01(value: float) -> float:
    """Clamp a value into the ``[0.0, 1.0]`` range."""
    return 0.0 if value < 0 else 1.0 if value > 1 else value


def meter_row(ratio: float, width: int) -> str:
    """Return a ``width``-wide bar filled left-to-right by ``ratio`` in ``[0, 1]``.

    A ratio at or above 1 fills the whole bar; at or below 0 the bar is empty.
    """
    if width <= 0:
        return ""
    filled = round(_clamp01(ratio) * width)
    return _FILLED * filled + _EMPTY * (width - filled)
