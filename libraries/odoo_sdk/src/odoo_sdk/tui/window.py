"""Pure date-window controller for the TUI.

:class:`DateWindow` is an immutable ``[start, end]`` inclusive date range with the
four move operations the arrow keys drive: Left/Right shift the start earlier or
later, Down/Up shift the end earlier or later, each by a one-day step. Every move
returns a new window and preserves the ``start <= end`` invariant — a move that
would cross the far edge is clamped to keep the window at least one day wide, so
the controller can never enter an invalid state. It performs no I/O; a driver
re-queries whenever the returned window differs from the current one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

_ONE_DAY = timedelta(days=1)


@dataclass(frozen=True)
class DateWindow:
    """An inclusive ``[start, end]`` date window with clamped one-day moves."""

    start: date
    end: date

    def __post_init__(self) -> None:
        """Reject a window whose start is after its end."""
        if self.start > self.end:
            raise ValueError("DateWindow requires start <= end")

    @property
    def days(self) -> int:
        """Return the inclusive width of the window in days (always >= 1)."""
        return (self.end - self.start).days + 1

    def move_start_earlier(self) -> "DateWindow":
        """Shift the start one day earlier, widening the window."""
        return DateWindow(self.start - _ONE_DAY, self.end)

    def move_start_later(self) -> "DateWindow":
        """Shift the start one day later, clamped so it never passes the end."""
        new_start = self.start + _ONE_DAY
        if new_start > self.end:
            new_start = self.end
        return DateWindow(new_start, self.end)

    def move_end_later(self) -> "DateWindow":
        """Shift the end one day later, widening the window."""
        return DateWindow(self.start, self.end + _ONE_DAY)

    def move_end_earlier(self) -> "DateWindow":
        """Shift the end one day earlier, clamped so it never passes the start."""
        new_end = self.end - _ONE_DAY
        if new_end < self.start:
            new_end = self.start
        return DateWindow(self.start, new_end)

    def with_start(self, value: date) -> "DateWindow":
        """Return a window with a new ``start`` (clamped to at most ``end``)."""
        return DateWindow(min(value, self.end), self.end)

    def with_end(self, value: date) -> "DateWindow":
        """Return a window with a new ``end`` (clamped to at least ``start``)."""
        return DateWindow(self.start, max(value, self.start))

    def start_iso(self) -> str:
        """Return the start date as an ISO ``YYYY-MM-DD`` string."""
        return self.start.isoformat()

    def end_iso(self) -> str:
        """Return the end date as an ISO ``YYYY-MM-DD`` string."""
        return self.end.isoformat()


# Arrow-key action names mapped to the DateWindow move they invoke. Keeping this
# as data (not a curses keymap) lets the controller be driven and tested without
# a terminal; the driver translates real curses key codes to these names.
WINDOW_ACTIONS = {
    "left": "move_start_earlier",
    "right": "move_start_later",
    "up": "move_end_later",
    "down": "move_end_earlier",
}


def apply_action(window: DateWindow, action: str) -> DateWindow:
    """Apply a named arrow action to ``window``, returning the new window.

    Unknown actions leave the window unchanged, so a driver can forward every
    keystroke without pre-filtering.
    """
    method_name = WINDOW_ACTIONS.get(action)
    if method_name is None:
        return window
    return getattr(window, method_name)()
