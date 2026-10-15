"""Cross-process file lock (replicas on the same volume): `<file>.lock` with flock/msvcrt, reentrant."""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_held: dict[str, int] = {}
_mu = threading.RLock()


def _os_lock(fd: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(fd, 0, 0)
        msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX)


def _os_unlock(fd: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(fd, 0, 0)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def _reenter(key: str) -> Iterator[None]:
    _held[key] += 1
    try:
        yield
    finally:
        _held[key] -= 1


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """Exclusive and blocking; only for short synchronous sections (no `await` inside)."""
    key = str(path)
    with _mu:
        if _held.get(key):
            with _reenter(key):
                yield
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(f"{path}.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            _os_lock(fd)
            _held[key] = 1
            try:
                yield
            finally:
                _held.pop(key, None)
                _os_unlock(fd)
        finally:
            os.close(fd)
