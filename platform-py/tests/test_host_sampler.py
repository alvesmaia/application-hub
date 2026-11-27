"""admin/runtime: the 5-minute host chart is already filled when the page opens (samples are taken in the
background, not only while someone polls the page)."""

from __future__ import annotations

import asyncio
from typing import Any, override

from hub.ops import host_chart
from hub.ops.host_sampler import HostSampler


class FakeOps:
    """Named fake: the runtime probe (no containers measured)."""

    def __init__(self) -> None:
        self.eng = self
        self.probes = 0

    async def probe(self) -> dict[str, Any]:
        self.probes += 1
        return {"reachable": False, "ncpu": 2, "mem_total": 4 * 1024**3}


def test_a_tick_adds_a_sample_without_the_page() -> None:
    host_chart.HISTORY._s.clear()
    ops = FakeOps()
    asyncio.run(HostSampler(ops).tick())
    assert ops.probes == 1 and len(host_chart.HISTORY._s) == 1


def test_the_loop_samples_until_stopped() -> None:
    async def scenario() -> int:
        ops = FakeOps()
        s = HostSampler(ops, every=0.01)
        s.start()
        await asyncio.sleep(0.08)
        await s.stop()
        return ops.probes

    assert asyncio.run(scenario()) >= 2


def test_a_failing_probe_does_not_stop_the_loop() -> None:
    class Broken(FakeOps):
        @override
        async def probe(self) -> dict[str, Any]:
            self.probes += 1
            raise RuntimeError("daemon down")

    async def scenario() -> int:
        ops = Broken()
        s = HostSampler(ops, every=0.01)
        s.start()
        await asyncio.sleep(0.06)
        await s.stop()
        return ops.probes

    assert asyncio.run(scenario()) >= 2
