"""admin/runtime keeps the host chart's last 5 minutes: a sample every few seconds in the background, so the page
opens with the history already drawn (before, samples were only taken while someone polled the page)."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

EVERY_S = 5.0


class HostSampler:
    def __init__(self, ops: Any, every: float = EVERY_S) -> None:
        self.ops, self.every = ops, every
        self.task: asyncio.Task[None] | None = None

    async def tick(self) -> None:
        """One sample: the same reading the panel draws (the runtime probe is cached; /proc is cheap)."""
        from hub.ops.routes_pages import host_panel  # the panel's context builder owns the reading

        await host_panel(self.ops, await self.ops.eng.probe())

    async def _loop(self) -> None:
        while True:
            with contextlib.suppress(Exception):  # a daemon or /proc hiccup must not end the sampling
                await self.tick()
            await asyncio.sleep(self.every)

    def start(self) -> None:
        self.task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
