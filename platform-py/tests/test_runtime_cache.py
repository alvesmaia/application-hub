"""Containers: cached probe (TTL), concurrent reads and off the event loop."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any

from hub.ops import runtime as rt


class _C:
    def __init__(self, n: int) -> None:
        self.name, self.status, self.attrs, self.image = f"app-hub-app-a{n}", "running", {}, None

    def stats(self, stream: bool = False) -> dict[str, Any]:
        time.sleep(0.2)
        return {}


def _engine(calls: list[int]) -> rt.Engine:
    def factory() -> Any:
        calls.append(1)
        cs = [_C(i) for i in range(5)]
        return SimpleNamespace(
            version=lambda: {"Version": "1"}, info=lambda: {"NCPU": 2, "MemTotal": 1},
            containers=SimpleNamespace(list=lambda **_: cs, get=lambda n: cs[0]),
        )  # fmt: skip

    return rt.Engine(factory)


async def test_probe_cached_and_parallel() -> None:
    calls: list[int] = []
    e = _engine(calls)
    t = time.monotonic()
    info = await e.probe()
    assert len(info["live"]) == 5 and time.monotonic() - t < 0.9  # 5 stats of 0.2 s in parallel
    t = time.monotonic()
    assert await e.probe() is info and time.monotonic() - t < 0.05
    assert len(calls) == 1
    await e.probe(0)
    assert len(calls) == 2


async def test_probe_stale_served_without_blocking_loop() -> None:
    calls: list[int] = []
    e = _engine(calls)
    first = await e.probe()
    e._cache = (time.monotonic() - 99, first)  # vencido
    ticks = 0

    async def beat() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    b = asyncio.create_task(beat())
    t = time.monotonic()
    assert await e.probe() is first and time.monotonic() - t < 0.05
    assert e._flight
    await asyncio.shield(e._flight)  # background refresh
    b.cancel()
    assert ticks > 5 and e._cache[1] is not first


async def test_probe_single_flight_and_invalidate() -> None:
    calls: list[int] = []
    e = _engine(calls)
    await asyncio.gather(*(e.probe() for _ in range(5)))
    assert len(calls) == 1
    e.invalidate()
    await e.probe()
    assert len(calls) == 2
