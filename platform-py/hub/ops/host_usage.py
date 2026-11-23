"""Host usage read from /proc. Inside the hub container on Linux, /proc/stat and /proc/meminfo are the host's (they
are not namespaced), so the Containers page can show what the whole machine uses, not only the hub's containers."""

from __future__ import annotations

import time
from pathlib import Path

KIB_PER_GIB = 1024 * 1024


def parse_stat(text: str) -> tuple[int, int] | None:
    """(busy, total) jiffies of the aggregate `cpu` line; idle and iowait are not busy."""
    first = text.split("\n", 1)[0].split()
    if len(first) < 5 or first[0] != "cpu" or not all(x.isdigit() for x in first[1:]):
        return None
    vals = [int(x) for x in first[1:]]
    total = sum(vals[:8])  # user nice system idle iowait irq softirq steal (guest is already in user)
    return total - vals[3] - vals[4], total


def parse_meminfo(text: str) -> float | None:
    """Used memory in GiB: MemTotal - MemAvailable."""
    kv = {k.strip(): v.split()[0] for k, v in (ln.split(":", 1) for ln in text.splitlines() if ":" in ln)}
    if "MemTotal" not in kv or "MemAvailable" not in kv:
        return None
    return (int(kv["MemTotal"]) - int(kv["MemAvailable"])) / KIB_PER_GIB


class HostUsage:
    """CPU fraction (0..1) since the previous sample and used memory (GiB); (None, None) without /proc."""

    def __init__(self, root: Path = Path("/proc"), settle_s: float = 0.25) -> None:
        self.root, self.settle_s = root, settle_s
        self._last: tuple[int, int] | None = None

    def _read(self, name: str) -> str:
        try:
            return (self.root / name).read_text(encoding="ascii", errors="replace")
        except OSError:
            return ""

    def sample(self) -> tuple[float | None, float | None]:
        """Blocking (a short wait on the very first call): run it in a thread."""
        if self._last is None and (first := parse_stat(self._read("stat"))):
            self._last = first
            time.sleep(self.settle_s)
        now = parse_stat(self._read("stat"))
        cpu = None
        if now and self._last and now[1] > self._last[1]:
            cpu = (now[0] - self._last[0]) / (now[1] - self._last[1])
        self._last = now or self._last
        return cpu, parse_meminfo(self._read("meminfo"))
