"""Graceful shutdown (SIGTERM from ``docker stop``/redeploy, Ctrl+C).

With uvicorn, the order is:

1. SIGTERM → :meth:`Shutdown.chain_signals` fires the signal **immediately** (the jobs worker
   stops taking new ones; the running job finishes the current batch and returns the
   lease without spending an attempt) and forwards the signal to uvicorn;
2. uvicorn stops accepting connections and drains the open ones;
3. in the exit ``lifespan``, :meth:`Shutdown.drain` waits for the work in progress for a
   shorter deadline than the container's ``stop_timeout`` (10 s in Docker).

SIGKILL/OOM do not go through here: the expired lease applies then (see :mod:`hub_sdk.jobs`).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import threading
import time
from collections.abc import Callable
from types import FrameType
from typing import Any

log = logging.getLogger("hub_sdk.shutdown")

# Process hooks: called (once) when any Shutdown of this process fires —
# e.g. forwarding SIGTERM to the jobs process (``HUB_JOBS_MODE=process``) right away, so
# both drain in parallel within the 10 s of ``docker stop``.
_PROCESS_HOOKS: list[Callable[[], None]] = []
_HOOKS_LOCK = threading.Lock()


def on_process_shutdown(fn: Callable[[], None]) -> None:
    with _HOOKS_LOCK:
        _PROCESS_HOOKS.append(fn)


def _run_process_hooks() -> None:
    with _HOOKS_LOCK:
        hooks = list(_PROCESS_HOOKS)
        _PROCESS_HOOKS.clear()
    for fn in hooks:
        try:
            fn()
        except Exception:
            log.exception("gancho de desligamento falhou")


class InFlight:
    def __init__(self, sd: Shutdown) -> None:
        self._sd = sd
        self._done = False

    def done(self) -> None:
        if not self._done:
            self._done = True
            with self._sd._lock:
                self._sd._inflight -= 1

    def __enter__(self) -> InFlight:
        return self

    def __exit__(self, *_: object) -> None:
        self.done()


class Shutdown:
    def __init__(self) -> None:
        self._flag = threading.Event()
        self._lock = threading.Lock()
        self._inflight = 0
        self._waiters: list[tuple[asyncio.AbstractEventLoop, asyncio.Event]] = []

    def trigger(self) -> None:
        if self._flag.is_set():
            return
        self._flag.set()
        _run_process_hooks()
        with self._lock:
            waiters = list(self._waiters)
        for loop, ev in waiters:
            with contextlib.suppress(RuntimeError):  # loop already closed
                loop.call_soon_threadsafe(ev.set)

    @property
    def triggered(self) -> bool:
        return self._flag.is_set()

    async def wait(self) -> None:
        """Resolves when the shutdown is triggered."""
        if self._flag.is_set():
            return
        ev = asyncio.Event()
        entry = (asyncio.get_running_loop(), ev)
        with self._lock:
            self._waiters.append(entry)
        try:
            if self._flag.is_set():
                return
            await ev.wait()
        finally:
            with self._lock:
                if entry in self._waiters:
                    self._waiters.remove(entry)

    def track(self) -> InFlight:
        with self._lock:
            self._inflight += 1
        return InFlight(self)

    @property
    def inflight(self) -> int:
        with self._lock:
            return self._inflight

    async def drain(self, timeout: float) -> bool:
        """Waits for the work in progress to finish (up to ``timeout`` s); ``True`` if it drained."""
        deadline = time.monotonic() + timeout
        while self.inflight > 0:
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(0.05)
        return True

    def chain_signals(self) -> None:
        """Chains SIGTERM/SIGINT: triggers this shutdown and calls the previous handler
        (uvicorn's). Call in the entry lifespan (after uvicorn
        has installed its own). Outside the main thread it does nothing."""
        if threading.current_thread() is not threading.main_thread():
            return
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                prev = signal.getsignal(sig)
            except (ValueError, OSError):
                continue

            def handler(
                signum: int,
                frame: FrameType | None,
                prev: Any = prev,
            ) -> None:
                log.warning("hub_sdk: sinal %s recebido; drenando", signum)
                self.trigger()
                if callable(prev):
                    prev(signum, frame)
                elif prev == signal.SIG_DFL and signum == signal.SIGINT:
                    raise KeyboardInterrupt

            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):
                continue


SignalHandler = Callable[[int, FrameType | None], Any]
