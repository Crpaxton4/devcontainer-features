"""Advisory file locks under ``<home>/locks``.

Linux only: the locks use ``fcntl.flock``. The kernel drops a lock when its
holder exits, so a crash never leaves a stale lock. The lock file is never
deleted, because deleting it races with a second opener.
"""

from __future__ import annotations

import fcntl
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .home import ensure_home

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_RETRY_SECONDS = 0.05


class LockHeldError(RuntimeError):
    """Another holder owns the lock."""

    def __init__(self, name: str, path: Path) -> None:
        super().__init__(f"lock {name!r} is held ({path})")
        self.name = name
        self.path = path


def _acquire(fd: int, name: str, path: Path, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise LockHeldError(name, path) from None
            time.sleep(_RETRY_SECONDS)


@contextmanager
def exclusive(
    name: str, *, home: Path | None = None, timeout: float = 0.0
) -> Iterator[Path]:
    """Hold the exclusive lock ``name``. Wait up to ``timeout`` seconds."""
    if not _NAME.fullmatch(name):
        raise ValueError(f"invalid lock name: {name!r}")
    path = ensure_home(home).locks / f"{name}.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        _acquire(fd, name, path, timeout)
        try:
            os.ftruncate(fd, 0)
            os.write(fd, f"{os.getpid()}\n".encode())
            yield path
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
