"""Per-container usage read from the container's own /proc, for engines that cannot measure each container (rootless
Podman with cgroupfs and no cgroup delegation reports the user's whole cgroup for every container). The container's PID
namespace holds only its processes: CPU = ticks between two readings, memory = sum of the RSS."""

from __future__ import annotations

from typing import Any

WINDOW_S = 0.5
CLK_TCK = 100  # USER_HZ of Linux on every architecture the hub runs on
PAGE = 4096
SCRIPT = (
    f"cat /proc/[0-9]*/stat 2>/dev/null; echo ===; sleep {WINDOW_S}; "
    "cat /proc/[0-9]*/stat 2>/dev/null; echo ===; cat /proc/[0-9]*/statm 2>/dev/null"
)
Usage = tuple[float, int, int]  # (vCPU, memory bytes, processes)


def _ticks(lines: list[str]) -> dict[str, int]:
    """pid → utime + stime (fields 14 and 15; the command name may hold spaces, so count after its ")")."""
    out: dict[str, int] = {}
    for ln in lines:
        pid, _, rest = ln.partition(" (")
        f = rest.rpartition(") ")[2].split()
        if pid.isdigit() and len(f) > 12:
            out[pid] = int(f[11]) + int(f[12])
    return out


def parse(text: str, window_s: float = WINDOW_S, clk_tck: int = CLK_TCK, page: int = PAGE) -> Usage | None:
    """`SCRIPT`'s output → (vCPU, bytes, processes); None when it is not that output (no shell, no /proc)."""
    parts = [p.strip().splitlines() for p in text.split("===")]
    if len(parts) != 3:
        return None
    before, after = _ticks(parts[0]), _ticks(parts[1])
    if not after:
        return None
    used = sum(t - before.get(p, t) for p, t in after.items())
    rss = sum(int(ln.split()[1]) for ln in parts[2] if len(ln.split()) > 1 and ln.split()[1].isdigit())
    return max(used, 0) / clk_tck / window_s, rss * page, len(after)


def measure(container: Any) -> Usage | None:
    """Runs `SCRIPT` in the container (`exec`); None when it cannot (no `sh`, exec refused)."""
    try:
        r = container.exec_run(["sh", "-c", SCRIPT], demux=False)
    except Exception:
        return None
    if r.exit_code not in (0, None):
        return None
    return parse(bytes(r.output or b"").decode("utf-8", "replace"))


def fill(live: list[dict[str, Any]], containers: list[Any]) -> bool:
    """Replaces the engine's numbers of the running containers with the /proc ones (in parallel); stopped ones use
    nothing. True when every running container was measured."""
    from concurrent.futures import ThreadPoolExecutor

    by = {c.name: c for c in containers}
    run = [x for x in live if x.get("state") == "running" and x["name"] in by]
    with ThreadPoolExecutor(max_workers=min(16, len(run) or 1)) as ex:
        got = list(ex.map(lambda x: measure(by[x["name"]]), run))
    for x, u in zip(run, got, strict=True):
        x.update(cpu_used=u[0], mem_used=u[1], pids=u[2]) if u else x.update(
            cpu_used=None, mem_used=None, pids=None
        )
    return all(got)
