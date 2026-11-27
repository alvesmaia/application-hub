"""Where the engine cannot measure each container (rootless Podman, no cgroup delegation) the hub measures it from
the container's own /proc (its PID namespace = its processes): CPU from two tick readings, memory from the RSS."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, override

from fastapi.testclient import TestClient
from test_ops import FakeDocker, hub  # noqa: F401 (fixture)
from test_runtime_metrics import SharedCgroupContainer, SharedCgroupDocker, use

from hub.ops import container_proc as cp

PAGE = 4096


def _stat(pid: int, utime: int, stime: int) -> str:
    return f"{pid} (python app) S 0 1 1 0 -1 4194560 100 0 0 0 {utime} {stime} 0 0 20 0 1 0 100 1000 50"


# two readings 0.5 s apart: the app's 2 processes used 10 + 15 ticks (= 0.25 s of CPU in 0.5 s = 0.5 vCPU)
SAMPLE = "\n".join([
    _stat(1, 100, 20), _stat(7, 50, 5), "===",
    _stat(1, 108, 22), _stat(7, 63, 7), "===",
    "2000 3000 300 1 0 500 0", "1000 1000 100 1 0 200 0",
])  # fmt: skip


def test_parse_two_readings_and_the_rss() -> None:
    got = cp.parse(SAMPLE, window_s=0.5, clk_tck=100, page=PAGE)
    assert got is not None
    cpu, mem, pids = got
    assert round(cpu, 3) == 0.5
    assert mem == (3000 + 1000) * PAGE
    assert pids == 2


def test_parse_rejects_output_it_does_not_understand() -> None:
    assert cp.parse("sh: not found", 0.5, 100, PAGE) is None
    assert cp.parse("", 0.5, 100, PAGE) is None


class ProcContainer(SharedCgroupContainer):
    @override
    def exec_run(self, cmd: list[str], **kw: Any) -> Any:
        return SimpleNamespace(exit_code=0, output=SAMPLE.encode())


class ProcDocker(SharedCgroupDocker):
    @override
    def _list(self, all: bool = True, filters: Any = None) -> list[Any]:
        return [ProcContainer(self, n) for n in self.names]


def test_the_page_shows_x_over_y_measured_from_proc(hub: TestClient) -> None:  # noqa: F811
    use(hub, ProcDocker())
    live = hub.get("/api/ops/runtime").json()["engine"]["live"]
    assert live and all(x["cpu_used"] is not None and x["mem_used"] == 4000 * PAGE for x in live)
    page = hub.get("/admin/runtime").text
    dash = [page[max(0, i - 160) : i + 20] for i in range(len(page)) if page.startswith("- /", i)]
    assert not dash, dash
    assert "16 / " in page  # 16 MiB measured


def test_without_a_shell_in_the_container_it_stays_unmeasured(hub: TestClient) -> None:  # noqa: F811
    use(hub, SharedCgroupDocker())  # exec_run returns nothing usable
    live = hub.get("/api/ops/runtime").json()["engine"]["live"]
    assert all(x["mem_used"] is None for x in live)


def test_platform_rows_use_x_over_the_limit_and_zero_when_stopped() -> None:
    from hub.ops.runtime_view import platform_view

    MIB = 1 << 20
    run = {"name": "app-hub-app-db", "state": "running", "status": "Up 2 hours", "image": "postgres:16", "cpu_used": 0.25,
           "mem_used": 300 * MIB, "mem_limit": 16 << 30, "host": {"NanoCpus": 2_000_000_000, "Memory": 1024 * MIB}}  # fmt: skip
    v = platform_view(run)
    assert v["cpu"] == "0,25 / 2,00 vCPU" and v["mem"] == "300 / 1024 MiB"
    stopped = run | {"state": "exited", "cpu_used": None, "mem_used": None}
    v = platform_view(stopped)
    assert v["cpu"] == "0 / 2,00 vCPU" and v["mem"] == "0 / 1024 MiB"
    nolimit = run | {"host": {}}
    assert platform_view(nolimit)["cpu"] == "0,25 vCPU"  # no limit: only the usage (the host is the ceiling)
