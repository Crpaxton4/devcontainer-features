"""Pure utilisation scoring for the sessionization ETL.

The gap sweep selects the gap whose average hours-per-day maximises a piecewise
score: an under-utilisation penalty below ``b_low``, an exponential ramp in the
optimal band, and a dishonesty penalty above ``b_high``.
"""

from __future__ import annotations

import math

from .config import SessionizationConfig


def _score_below(x: float, config: SessionizationConfig) -> float:
    """Return the under-utilisation branch score for ``x < b_low``."""
    return config.s_low - math.expm1(config.k1 * (config.b_low - x))


def _score_optimal(x: float, config: SessionizationConfig) -> float:
    """Return the optimal-band score for ``b_low <= x <= b_high``."""
    denom = math.expm1(config.k2 * (config.b_high - config.b_low))
    span = config.s_high - config.s_low
    if denom == 0:
        width = config.b_high - config.b_low
        t = (x - config.b_low) / width if width > 0 else 0.0
        return config.s_low + span * t
    return config.s_low + span * math.expm1(config.k2 * (x - config.b_low)) / denom


def _score_above(x: float, config: SessionizationConfig) -> float:
    """Return the dishonesty branch score for ``x > b_high``."""
    return config.s_high - math.expm1(config.k3 * (x - config.b_high))


def score_gap(total_secs: float, num_days: int, config: SessionizationConfig) -> float:
    """Return the piecewise utilisation score for a total over ``num_days``.

    ``x = total_secs / 3600 / num_days`` is the average hours worked per day.
    The function is C0-continuous: ``f(b_low) == s_low`` and ``f(b_high) == s_high``.
    """
    x = total_secs / 3600.0 / max(num_days, 1)
    if x < config.b_low:
        return _score_below(x, config)
    if x <= config.b_high:
        return _score_optimal(x, config)
    return _score_above(x, config)


def score_day(total_secs: float, config: SessionizationConfig) -> float:
    """Return the score for one target day's total seconds."""
    return score_gap(total_secs, 1, config)
