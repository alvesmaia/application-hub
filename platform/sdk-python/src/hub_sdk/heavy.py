"""Limit on simultaneous heavy operations per app (import, export, report).

Without a slot, **immediate** 429 with ``Retry-After`` (``hub.js`` shows
"queued…" and retries on its own). The slot is released when the *permit* is released —
in a streaming download, only at the end of the body.

Note (Python): the semaphore applies **per process**. With ``uvicorn --workers N`` the
effective app limit is ``N × HUB_HEAVY_MAX`` — the recommended sizing
(``loadtest/RESULTADOS.md``, wave 7) is 1 worker per vCPU.
"""

from __future__ import annotations

import contextlib
import os
import threading
import weakref
from collections.abc import AsyncIterator
from types import TracebackType
from typing import Any

from starlette.responses import StreamingResponse
from typing_extensions import override

DEFAULT_MAX = 2


class Permit:
    """Slot taken; ``release()`` (or leaving the ``with``) returns it. Releasing twice is
    inócuo."""

    def __init__(self, heavy: Heavy) -> None:
        self._heavy = heavy
        self._released = False

    def release(self) -> None:
        with self._heavy._lock:
            if self._released:
                return
            self._released = True
            self._heavy._used -= 1

    def __enter__(self) -> Permit:
        return self

    def __exit__(
        self,
        et: type[BaseException] | None,
        e: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()


class Heavy:
    def __init__(self, max_: int = DEFAULT_MAX) -> None:
        self.max = max(1, max_)
        self._used = 0
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls) -> Heavy:
        try:
            n = int(os.environ.get("HUB_HEAVY_MAX", DEFAULT_MAX))
        except ValueError:
            n = DEFAULT_MAX
        return cls(n)

    def try_acquire(self) -> Permit | None:
        """Slot without waiting; ``None`` = limit reached (answer 429)."""
        with self._lock:
            if self._used >= self.max:
                return None
            self._used += 1
        return Permit(self)

    @property
    def in_use(self) -> int:
        with self._lock:
            return self._used


class PermitStreamingResponse(StreamingResponse):
    """``StreamingResponse`` that **always** returns the heavy-operation slot:

    - at the end of ``__call__`` (body sent, error, client disconnected before or during the
      send, cancellation by disconnection) — and closes the generator, releasing the DB connection;
    - if the response is never sent (e.g. an exception in a middleware before sending), when
      the object is collected (``weakref.finalize``).

    Releasing more than once is harmless (:meth:`Permit.release`)."""

    def __init__(self, content: AsyncIterator[bytes], permit: Permit, **kw: Any) -> None:
        super().__init__(content, **kw)
        self._gen = content
        self.permit = permit
        weakref.finalize(self, permit.release)

    @override
    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.permit.release()
            aclose = getattr(self._gen, "aclose", None)
            if aclose is not None:
                with contextlib.suppress(Exception):
                    await aclose()
