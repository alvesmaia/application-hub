"""Last 5 minutes of the host's CPU and memory for admin/runtime: samples kept in this process (taken every 5 s in
the background by `host_sampler`, and on each panel poll) and drawn as a stacked area SVG (system, platform, apps)."""

from __future__ import annotations

import threading
from collections import deque
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from hub.ops.opsctx import br

WINDOW_S = 300.0
MIN_GAP_S = 4.0  # several viewers poll the same panel: one sample per poll period
W, H = 400.0, 100.0
_GMT3 = timezone(timedelta(hours=-3))  # same fixed GMT-3 as the other screens
_NAMES = ("Sistema", "Plataforma", "Aplicações")
Parts = tuple[float, float, float]  # system, platform, apps


class HostHistory:
    def __init__(self) -> None:
        self._s: deque[tuple[float, Parts, Parts]] = deque(maxlen=int(WINDOW_S / MIN_GAP_S) + 2)
        self._lock = threading.Lock()

    def add(self, t: float, cpu: Parts, mem: Parts) -> None:
        with self._lock:
            if self._s and t - self._s[-1][0] < MIN_GAP_S:
                return
            self._s.append((t, cpu, mem))

    def window(self, now: float) -> list[tuple[float, Parts, Parts]]:
        with self._lock:
            return [s for s in self._s if now - s[0] <= WINDOW_S]


HISTORY = HostHistory()


def _pts(xs: list[float], ys: list[float]) -> str:
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys, strict=True))


def _slices(
    points: list[tuple[float, Parts]], xs: list[float], total: float, unit: str
) -> list[tuple[float, float, str]]:
    """One hover strip per sample, from the midpoint with the previous one to the midpoint with the next one."""
    out = []
    for i, (t, parts) in enumerate(points):
        lo = (xs[i - 1] + xs[i]) / 2 if i else max(xs[i] - 4, 0.0)
        hi = (xs[i] + xs[i + 1]) / 2 if i + 1 < len(xs) else W
        at = datetime.fromtimestamp(t, UTC).astimezone(_GMT3).strftime("%H:%M:%S")
        each = " · ".join(f"{n} {br(v, 2)}" for n, v in zip(_NAMES, parts, strict=True))
        out.append((lo, hi - lo, f"{at} · Total {br(sum(parts), 2)} / {br(total, 2)} {unit} · {each}"))
    return out


def chart(points: list[tuple[float, Parts]], total: float, unit: str, now: float) -> dict[str, Any]:
    """Stacked area (polygons per part, bottom to top), the total line, the reference lines and the hover strips."""
    xs = [max(W * (t - (now - WINDOW_S)) / WINDOW_S, 0.0) for t, _ in points]
    y = lambda v: H - H * min(max(v, 0.0), total) / total if total else H  # noqa: E731
    acc = [0.0] * len(points)
    layers = []
    for k, cls in enumerate(("sys", "plat", "apps")):
        top = [a + p[k] for a, (_, p) in zip(acc, points, strict=True)]
        poly = _pts(xs, [y(v) for v in top]) + " " + _pts(xs[::-1], [y(v) for v in acc[::-1]])
        layers.append((cls, poly))
        acc = top
    return {"layers": layers, "total": _pts(xs, [y(v) for v in acc]), "grid": [H * (1 - k / 4) for k in range(1, 5)],
            "slices": _slices(points, xs, total, unit)}  # fmt: skip


def scale(total: float, unit: str) -> list[str]:
    """0, 25, 50, 75 and 100% of the host under the bar (the last one with the unit)."""
    marks = [br(total * k / 4, 0 if (total * k / 4).is_integer() else 1) for k in range(5)]
    return [*marks[:4], f"{marks[4]} {unit}"]
