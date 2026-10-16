"""The singleton lock (design section 7).

An exclusive **whole-file** advisory lock (``flock``) on ``dispatcher.lock``,
taken by the entry point before anything else and held open for the life of the
process. Whole-file, not record locking: the record flavour (``lockf`` /
``fcntl`` POSIX locks) is released the moment the process closes *any*
descriptor to the file. The lock file is separate from both databases, because
locking a database file collides with the engine's own locking.

There is no flag that skips it. Two dispatchers need two state roots.
"""

import fcntl
import os
from pathlib import Path


class AlreadyRunningError(RuntimeError):
    """Another dispatcher holds the lock on this state root."""


def _holder(fd: int) -> str:
    """The pid the holder wrote into the lock file, or ``unknown``."""
    try:
        text = os.pread(fd, 32, 0).decode().strip()
    except OSError:
        return "unknown"
    return text if text.isdigit() else "unknown"


class DispatcherLock:
    """Hold ``path`` exclusively until :meth:`release` (or process exit)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._fd: int | None = None

    def acquire(self) -> "DispatcherLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            holder = _holder(fd)
            os.close(fd)
            raise AlreadyRunningError(
                f"another dispatcher (pid {holder}) holds {self.path}; "
                "point a second dispatcher at a second state root"
            ) from exc
        self._fd = fd
        # Diagnostic only. The lock is the flock, never the file's content.
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode())
        return self

    def release(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    @property
    def held(self) -> bool:
        return self._fd is not None

    def __enter__(self) -> "DispatcherLock":
        return self.acquire()

    def __exit__(self, *exc_info) -> None:
        self.release()
