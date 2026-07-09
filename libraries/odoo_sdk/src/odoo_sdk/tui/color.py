"""Pure truecolor -> 256 -> 16 color quantization for the TUI.

Terminals vary in color depth. This module maps an ``(r, g, b)`` truecolor
triple down to the palette a terminal actually supports, as plain integers, so a
driver can pick the closest available color without any ``curses`` calls. Every
function is pure and total; nothing here touches a terminal.
"""

from __future__ import annotations

from enum import Enum


class ColorDepth(Enum):
    """The color capability of a target terminal."""

    TRUECOLOR = "truecolor"  # 24-bit direct RGB
    ANSI256 = "ansi256"  # xterm 256-color palette
    ANSI16 = "ansi16"  # classic 16-color palette


# The 6 canonical intensity steps of the xterm 6x6x6 color cube.
_CUBE_STEPS = (0, 95, 135, 175, 215, 255)

# RGB anchors for the classic 16-color palette, indexed by ANSI color number.
_ANSI16_RGB = (
    (0, 0, 0),  # 0 black
    (170, 0, 0),  # 1 red
    (0, 170, 0),  # 2 green
    (170, 85, 0),  # 3 yellow
    (0, 0, 170),  # 4 blue
    (170, 0, 170),  # 5 magenta
    (0, 170, 170),  # 6 cyan
    (170, 170, 170),  # 7 white
    (85, 85, 85),  # 8 bright black
    (255, 85, 85),  # 9 bright red
    (85, 255, 85),  # 10 bright green
    (255, 255, 85),  # 11 bright yellow
    (85, 85, 255),  # 12 bright blue
    (255, 85, 255),  # 13 bright magenta
    (85, 255, 255),  # 14 bright cyan
    (255, 255, 255),  # 15 bright white
)


def _clamp(value: int) -> int:
    """Clamp a channel value into the ``[0, 255]`` byte range."""
    return 0 if value < 0 else 255 if value > 255 else value


def _nearest_cube_step(value: int) -> int:
    """Return the index of the closest 6x6x6 cube intensity step."""
    value = _clamp(value)
    best_index = 0
    best_dist = 256
    for index, step in enumerate(_CUBE_STEPS):
        dist = abs(step - value)
        if dist < best_dist:
            best_dist = dist
            best_index = index
    return best_index


def to_ansi256(rgb: tuple[int, int, int]) -> int:
    """Map a truecolor triple to the closest xterm 256-color index.

    Grays collapse onto the 24-step grayscale ramp when that is closer than the
    color cube; otherwise the nearest 6x6x6 cube cell is chosen.
    """
    r, g, b = (_clamp(c) for c in rgb)
    ri, gi, bi = (_nearest_cube_step(c) for c in (r, g, b))
    cube_index = 16 + 36 * ri + 6 * gi + bi
    cube_rgb = tuple(_CUBE_STEPS[i] for i in (ri, gi, bi))
    cube_dist = _sq_dist((r, g, b), cube_rgb)

    gray_avg = (r + g + b) // 3
    gray_level = min(23, max(0, round((gray_avg - 8) / 10)))
    gray_value = 8 + gray_level * 10
    gray_dist = _sq_dist((r, g, b), (gray_value, gray_value, gray_value))
    if gray_dist < cube_dist:
        return 232 + gray_level
    return cube_index


def to_ansi16(rgb: tuple[int, int, int]) -> int:
    """Map a truecolor triple to the closest classic 16-color index."""
    target = tuple(_clamp(c) for c in rgb)
    best_index = 0
    best_dist = None
    for index, anchor in enumerate(_ANSI16_RGB):
        dist = _sq_dist(target, anchor)
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best_index = index
    return best_index


def _sq_dist(a: tuple[int, int, int], b: tuple[int, int, int]) -> int:
    """Return the squared Euclidean distance between two RGB triples."""
    return sum((ac - bc) ** 2 for ac, bc in zip(a, b))


def quantize(
    rgb: tuple[int, int, int], depth: ColorDepth
) -> tuple[int, int, int] | int:
    """Quantize an RGB triple for ``depth``.

    Truecolor terminals get the (clamped) triple back unchanged; 256- and
    16-color terminals get the closest palette index as an integer.
    """
    if depth is ColorDepth.TRUECOLOR:
        return tuple(_clamp(c) for c in rgb)
    if depth is ColorDepth.ANSI256:
        return to_ansi256(rgb)
    return to_ansi16(rgb)
