"""Pure window computation for the sessionization ETL.

Partitions sorted timestamps into billable sessions separated by inactivity
gaps, applying per-session billing rounding and a minimum-duration floor.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - hints only, avoids a config import cycle
    from .config import SessionizationConfig


def ceil_to_billing_step(secs: float, config: "SessionizationConfig") -> float:
    """Round ``secs`` UP to the nearest ``billing_step_mins`` boundary."""
    step = config.billing_step_mins * 60.0
    if step <= 0 or secs <= 0:
        return secs
    return math.ceil(secs / step) * step


def _session_duration(elapsed: float, config: "SessionizationConfig") -> float:
    """Return the billed duration for a raw elapsed span (seconds)."""
    return max(ceil_to_billing_step(elapsed, config), float(config.min_task_secs))


def compute_windows(
    timestamps: list[datetime],
    gap_secs: int,
    config: "SessionizationConfig",
) -> list[tuple[datetime, datetime]]:
    """Partition sorted timestamps into sessions separated by gaps > gap_secs.

    Each session's elapsed time is rounded UP to the nearest billing step, then
    floored to ``min_task_secs``. The floor is applied once per SESSION.
    """
    if not timestamps:
        return []
    sorted_ts = sorted(timestamps)
    windows: list[tuple[datetime, datetime]] = []
    win_start = sorted_ts[0]
    for i in range(1, len(sorted_ts)):
        gap = (sorted_ts[i] - sorted_ts[i - 1]).total_seconds()
        if gap > gap_secs:
            elapsed = (sorted_ts[i - 1] - win_start).total_seconds()
            dur = _session_duration(elapsed, config)
            windows.append((win_start, win_start + timedelta(seconds=dur)))
            win_start = sorted_ts[i]
    elapsed = (sorted_ts[-1] - win_start).total_seconds()
    dur = _session_duration(elapsed, config)
    windows.append((win_start, win_start + timedelta(seconds=dur)))
    return windows
