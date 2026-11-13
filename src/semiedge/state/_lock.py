"""The exclusive single-writer lock (spec phase2-4-interfaces.md §5.3; issue #21).

``{db_dir}/engine.lock`` is locked with an OS-level, non-blocking exclusive lock (``fcntl.flock``
on POSIX, ``msvcrt.locking`` on Windows). The OS drops the lock if the process dies, so a crash
never leaves a stale lock behind. A second writer (another process, or a second ``StateStore``
in the same process) is refused with ``StateStoreError("writer_lock_held")``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from semiedge.state._reader import StateStoreError

__all__ = ["WRITER_LOCK_HELD", "WriterLock"]

WRITER_LOCK_HELD = "writer_lock_held"


class WriterLock:
    def __init__(self, path: Path) -> None:
        self._path = path
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            _lock(fd)
        except OSError:
            os.close(fd)
            raise StateStoreError(WRITER_LOCK_HELD) from None
        self._fd: int | None = fd

    @property
    def path(self) -> Path:
        return self._path

    def release(self) -> None:
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            _unlock(fd)
        finally:
            os.close(fd)


if sys.platform == "win32":
    import msvcrt

    def _lock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)
