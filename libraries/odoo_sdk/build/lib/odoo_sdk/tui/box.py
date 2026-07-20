"""Pure box-panel drawing for the TUI.

``draw_box`` returns the character rows of a rounded or square panel with an
optional title embedded in the top border, as plain strings a driver blits with
``addstr``. It computes only characters; it never touches a terminal. The result
is exactly ``height`` rows each of exactly ``width`` columns, so a driver can
place it at a fixed origin without measuring.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BoxChars:
    """The six glyphs that draw one box style (corners + edges)."""

    top_left: str
    top_right: str
    bottom_left: str
    bottom_right: str
    horizontal: str
    vertical: str


ROUNDED = BoxChars("╭", "╮", "╰", "╯", "─", "│")


def _title_segment(title: str, inner_width: int) -> str:
    """Return the horizontal top-border fill with ``title`` embedded.

    The title is wrapped in spaces and truncated to fit the interior; the rest of
    the border is horizontal rule. An empty title yields a plain rule.
    """
    rule = "─" * inner_width
    if not title or inner_width < 3:
        return rule
    label = f" {title} "
    if len(label) > inner_width:
        label = label[: inner_width - 1] + " "
    return label + "─" * (inner_width - len(label))


def draw_box(
    width: int,
    height: int,
    title: str = "",
    *,
    chars: BoxChars = ROUNDED,
) -> list[str]:
    """Return the ``height`` rows of a ``width``-wide panel border.

    :param width: Total panel width including both vertical borders (>= 2).
    :param height: Total panel height including both horizontal borders (>= 2).
    :param title: Optional title embedded in the top border.
    :param chars: The box style glyphs to draw with.
    :return: Exactly ``height`` strings, each exactly ``width`` characters wide.
    """
    if width < 2 or height < 2:
        raise ValueError("draw_box requires width >= 2 and height >= 2")
    inner_width = width - 2
    top = chars.top_left + _title_segment(title, inner_width) + chars.top_right
    bottom = chars.bottom_left + chars.horizontal * inner_width + chars.bottom_right
    middle = chars.vertical + " " * inner_width + chars.vertical
    return [top] + [middle] * (height - 2) + [bottom]


def place_text(row: str, text: str, col: int) -> str:
    """Overlay ``text`` onto ``row`` starting at column ``col``, clipped to width.

    Returns a new row string the same length as ``row``; characters of ``text``
    that would fall outside ``row`` are dropped. Useful for writing panel content
    into a pre-sized interior row without changing the row's width.
    """
    if col < 0:
        text = text[-col:]
        col = 0
    if col >= len(row) or not text:
        return row
    end = min(len(row), col + len(text))
    clipped = text[: end - col]
    return row[:col] + clipped + row[end:]
