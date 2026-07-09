"""Pure braille / block graph canvas for the TUI.

A :class:`GraphCanvas` is a fixed-width ring buffer of scalar samples. Pushing a
sample appends it on the right and drops the oldest on the left, so a driver can
paint a right-to-left scrolling graph. Rendering turns the buffered samples into
a grid of Unicode block/braille glyphs — pure data a driver blits with
``add_wch`` — computed from a 5x5 up/down glyph lookup table so a column can show
both a rising and falling half. No terminal state is touched here.
"""

from __future__ import annotations

from collections import deque
from typing import Iterable

# Vertical block glyphs by eighth (index 0 == empty, 8 == full block).
_BLOCKS = (" ", "▁", "▂", "▃", "▄", "▅", "▆", "▇", "█")

# Braille base and the dot bit for each (col, row) within a 2x4 braille cell.
_BRAILLE_BASE = 0x2800
_BRAILLE_DOTS = (
    (0x01, 0x08),
    (0x02, 0x10),
    (0x04, 0x20),
    (0x40, 0x80),
)


def _block_glyph(eighths: int) -> str:
    """Return the vertical block glyph for a height of ``eighths`` (0..8)."""
    eighths = 0 if eighths < 0 else 8 if eighths > 8 else eighths
    return _BLOCKS[eighths]


def _column_eighths(value: float, ceiling: float, rows: int) -> list[int]:
    """Return the per-row fill in eighths for one column, bottom row first.

    ``value`` is scaled against ``ceiling`` over ``rows`` character rows; each
    row holds 0..8 eighths so a tall bar fills whole blocks from the bottom and
    the top row shows the fractional remainder.
    """
    if ceiling <= 0 or rows <= 0:
        return [0] * max(rows, 0)
    fraction = value / ceiling
    fraction = 0.0 if fraction < 0 else 1.0 if fraction > 1 else fraction
    total_eighths = round(fraction * rows * 8)
    column: list[int] = []
    for _ in range(rows):
        column.append(min(8, total_eighths))
        total_eighths = max(0, total_eighths - 8)
    return column


def braille_cell(dots: Iterable[tuple[int, int]]) -> str:
    """Return the braille glyph lighting the given ``(col, row)`` dot positions.

    ``col`` is 0..1 and ``row`` is 0..3 within one 2x4 braille cell; out-of-range
    positions are ignored so callers can pass a raw scatter set.
    """
    code = _BRAILLE_BASE
    for col, row in dots:
        if 0 <= col <= 1 and 0 <= row <= 3:
            code |= _BRAILLE_DOTS[row][col]
    return chr(code)


def sparkline(values: list[float], ceiling: float | None = None) -> str:
    """Return a single-row block sparkline for ``values``.

    Each value maps to one of eight block heights scaled to ``ceiling`` (the max
    of ``values`` when omitted). An empty input yields an empty string.
    """
    if not values:
        return ""
    top = ceiling if ceiling is not None else max(values)
    if top <= 0:
        return _BLOCKS[0] * len(values)
    cells = []
    for value in values:
        fraction = value / top
        fraction = 0.0 if fraction < 0 else 1.0 if fraction > 1 else fraction
        cells.append(_block_glyph(round(fraction * 8)))
    return "".join(cells)


class GraphCanvas:
    """A fixed-width ring buffer of samples rendered as a block-glyph grid.

    :param width: Number of columns (samples) retained; the oldest is dropped
        once the buffer is full, so the graph scrolls right-to-left.
    :param height: Number of character rows in the rendered grid.
    """

    def __init__(self, width: int, height: int):
        if width <= 0 or height <= 0:
            raise ValueError("GraphCanvas width and height must be positive")
        self._width = width
        self._height = height
        self._samples: deque[float] = deque(maxlen=width)

    @property
    def width(self) -> int:
        """Return the canvas column count."""
        return self._width

    @property
    def height(self) -> int:
        """Return the canvas row count."""
        return self._height

    @property
    def samples(self) -> list[float]:
        """Return the buffered samples oldest-first."""
        return list(self._samples)

    def push(self, value: float) -> None:
        """Append one sample on the right, dropping the oldest when full."""
        self._samples.append(float(value))

    def resize(self, width: int, height: int) -> None:
        """Reflow to a new size, keeping the most recent samples that fit."""
        if width <= 0 or height <= 0:
            raise ValueError("GraphCanvas width and height must be positive")
        kept = list(self._samples)[-width:]
        self._width = width
        self._height = height
        self._samples = deque(kept, maxlen=width)

    def render(self, ceiling: float | None = None) -> list[str]:
        """Return the block-glyph grid, top row first and right-aligned.

        The newest sample sits in the rightmost column; unfilled leftmost columns
        are blank so the graph scrolls in from the right. ``ceiling`` sets the
        full-scale value (the max sample when omitted, or 1 when all zero).
        """
        samples = list(self._samples)
        top = ceiling if ceiling is not None else (max(samples) if samples else 0.0)
        if top <= 0:
            top = 1.0
        columns = [_column_eighths(value, top, self._height) for value in samples]
        pad = self._width - len(columns)
        empty = [0] * self._height
        grid_columns = [empty] * pad + columns
        rows: list[str] = []
        for row_from_top in range(self._height):
            row_from_bottom = self._height - 1 - row_from_top
            rows.append(
                "".join(_block_glyph(col[row_from_bottom]) for col in grid_columns)
            )
        return rows
