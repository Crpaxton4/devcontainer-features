"""Admission on measured available memory (design section 7).

Never a configured job count. The measured number is ``MemAvailable``: the
kernel's estimate of what a new workload can get. Free memory is the wrong
number, because the page cache counts as used on Linux and a busy machine
reads near zero free while gigabytes are reclaimable.

Admission is serial. Memory is consumed when a job's container and Odoo are
up, not when the job is admitted, so :class:`AdmissionGate` is used one job at a
time: measure, admit, bring the stack up, :meth:`AdmissionGate.settle`, and
only then measure again. A burst admitted together would all see the same
healthy number and all go.
"""

import time
from collections.abc import Callable
from pathlib import Path

MEMINFO = Path("/proc/meminfo")
_KB_PER_GB = 1024 * 1024


class MemoryReadError(RuntimeError):
    """The available-memory figure could not be read. The gate never guesses."""


def read_available_kb(meminfo: Path = MEMINFO) -> int:
    """Return ``MemAvailable`` in kB from a ``/proc/meminfo`` style file."""
    try:
        text = Path(meminfo).read_text(encoding="utf-8")
    except OSError as exc:
        raise MemoryReadError(f"cannot read {meminfo}: {exc}") from exc
    for line in text.splitlines():
        if line.startswith("MemAvailable:"):
            try:
                return int(line.split()[1])
            except (IndexError, ValueError) as exc:
                raise MemoryReadError(f"malformed line: {line!r}") from exc
    raise MemoryReadError(f"{meminfo} has no MemAvailable line")


class AdmissionGate:
    """Decide whether one more job fits, from measured available memory."""

    def __init__(
        self,
        min_available_gb: float,
        settle_s: float = 0.0,
        *,
        meminfo: Path = MEMINFO,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.min_available_kb = int(min_available_gb * _KB_PER_GB)
        self.settle_s = settle_s
        self._meminfo = meminfo
        self._sleep = sleep
        self.last_available_kb: int | None = None  # the last good reading

    def available_kb(self) -> int:
        self.last_available_kb = read_available_kb(self._meminfo)
        return self.last_available_kb

    def has_room(self) -> bool:
        """True when measured available memory is at or above the threshold."""
        return self.available_kb() >= self.min_available_kb

    def settle(self) -> None:
        """Let the machine account for a newly admitted stack before measuring."""
        if self.settle_s > 0:
            self._sleep(self.settle_s)
