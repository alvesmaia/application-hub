"""Minimal in-process telemetry (`hub-telemetry` + SLOs): RED metrics per app x environment, traces and SLOs.

10 s buckets (1 h, charts) and 5 min buckets (30 days, SLOs/status; stored in `metrics.json`); duration in
a histogram of fixed buckets. `HUB_TELEMETRY=0` turns collection off (the gateways keep serving).
"""

from __future__ import annotations

import json
import os
import secrets
import time
from bisect import bisect_left
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hub.filelock import file_lock

BOUNDS = (5.0, 10.0, 25.0, 50.0, 100.0, 250.0, 500.0, 1000.0, 2500.0, 5000.0, 10000.0, 30000.0)
NB = len(BOUNDS) + 1
FINE_S, FINE_KEEP, COARSE_S, COARSE_KEEP = 10, 360, 300, 30 * 24 * 12
FAST_BURN, MIN_REQUESTS = 14.4, 20


def enabled() -> bool:
    return os.environ.get("HUB_TELEMETRY") != "0"


def now_s() -> int:
    return int(time.time())


@dataclass
class Bucket:
    t: int
    count: int = 0
    errors: int = 0
    sum_ms: float = 0.0
    hist: list[int] = field(default_factory=lambda: [0] * NB)
    probes_ok: int = 0
    probes: int = 0

    def add(self, err: bool, ms: float) -> None:
        self.count += 1
        self.errors += err
        self.sum_ms += ms
        self.hist[bisect_left(BOUNDS, ms)] += 1

    def merge(self, o: Bucket) -> None:
        self.count += o.count
        self.errors += o.errors
        self.sum_ms += o.sum_ms
        self.hist = [a + b for a, b in zip(self.hist, o.hist, strict=True)]
        self.probes_ok += o.probes_ok
        self.probes += o.probes


def percentile(hist: list[int], q: float) -> float | None:
    total = sum(hist)
    if not total:
        return None
    target, acc = max(q * total, 1.0), 0.0
    for i, c in enumerate(hist):
        if c and acc + c >= target:
            lo = BOUNDS[i - 1] if i else 0.0
            hi = BOUNDS[i] if i < len(BOUNDS) else lo * 2
            return lo + (hi - lo) * ((target - acc) / c)
        acc += c
    return BOUNDS[-1]


def fraction_under(hist: list[int], ms: float) -> float | None:
    total = sum(hist)
    if not total:
        return None
    under = 0.0
    for i, c in enumerate(hist):
        lo = BOUNDS[i - 1] if i else 0.0
        hi = BOUNDS[i] if i < len(BOUNDS) else float("inf")
        if ms >= hi:
            under += c
        elif ms > lo and hi != float("inf"):
            under += c * (ms - lo) / (hi - lo)
    return under / total


@dataclass
class Window:
    requests: int = 0
    errors: int = 0
    availability: float = 1.0
    p95: float | None = None
    probes: int = 0
    probes_ok: int = 0
    hist: list[int] = field(default_factory=lambda: [0] * NB)

    def under(self, ms: float) -> float | None:
        return fraction_under(self.hist, ms)


@dataclass
class Series:
    fine: deque[Bucket] = field(default_factory=deque)
    coarse: deque[Bucket] = field(default_factory=deque)
    gauges: dict[int, dict[str, Any]] = field(default_factory=dict)
    inflight: int = 0


def _slot(q: deque[Bucket], t: int, keep: int) -> Bucket:
    if not q or q[-1].t != t:
        q.append(Bucket(t))
        while len(q) > keep:
            q.popleft()
    return q[-1]


class Metrics:
    def __init__(self, directory: Path | None = None) -> None:
        self.path = directory / "metrics.json" if directory else None
        self.series: dict[str, Series] = {}
        self.listener: Callable[[str, str, int, float], None] | None = None
        # Replicas on the same volume: what this replica added since the last write (series, t) → increment.
        self._pending: dict[tuple[str, int], Bucket] = {}
        self._mtime = 0
        for k, rows in self._read().items():
            self.series[k] = Series(coarse=rows)

    def _read(self) -> dict[str, deque[Bucket]]:
        if not (self.path and self.path.is_file()):
            return {}
        try:
            self._mtime = self.path.stat().st_mtime_ns
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return {k: deque(Bucket(**r) for r in rows) for k, rows in raw.items()}
        except (OSError, ValueError, TypeError):
            return {}

    def _pend(self, key: str, t: int) -> Bucket:
        return self._pending.setdefault((key, t), Bucket(t))

    def save(self) -> None:
        if not self.path:
            return
        with file_lock(self.path):
            try:
                other = self.path.stat().st_mtime_ns != self._mtime  # another replica wrote after us
            except OSError:
                other = False
            if other:  # adopts the file and adds on top only what this replica counted since the last write
                for k, rows in self._read().items():
                    self._s(*k.split("|", 1)).coarse = rows
                for (k, t), add in self._pending.items():
                    q = self._s(*k.split("|", 1)).coarse
                    b = next((x for x in reversed(q) if x.t == t), None)
                    if b is None:
                        q.append(b := Bucket(t))
                        if len(q) > 1 and q[-2].t > t:
                            q = self.series[k].coarse = deque(sorted(q, key=lambda x: x.t))
                    b.merge(add)
                    while len(q) > COARSE_KEEP:
                        q.popleft()
            data = {k: [b.__dict__ for b in s.coarse] for k, s in self.series.items()}
            tmp = self.path.with_suffix(f".json.{os.getpid()}.tmp")
            tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
            os.replace(tmp, self.path)
            self._mtime = self.path.stat().st_mtime_ns
            self._pending.clear()

    def _s(self, app: str, env: str) -> Series:
        return self.series.setdefault(f"{app}|{env}", Series())

    def observe(self, app: str, env: str, status: int, ms: float, now: int | None = None) -> None:
        if self.listener:
            self.listener(app, env, status, ms)
        now = now_s() if now is None else now
        s, err = self._s(app, env), status >= 500
        _slot(s.fine, now - now % FINE_S, FINE_KEEP).add(err, ms)
        tc = now - now % COARSE_S
        _slot(s.coarse, tc, COARSE_KEEP).add(err, ms)
        self._pend(f"{app}|{env}", tc).add(err, ms)

    def _gauge(self, s: Series, now: int) -> dict[str, Any]:
        t = now - now % FINE_S
        for old in [k for k in s.gauges if k < t - FINE_KEEP * FINE_S]:
            del s.gauges[old]
        return s.gauges.setdefault(t, {"inflight_max": 0, "cpu": None, "mem": None, "app": {}})

    def inflight_inc(self, app: str, env: str) -> None:
        s = self._s(app, env)
        s.inflight += 1
        g = self._gauge(s, now_s())
        g["inflight_max"] = max(g["inflight_max"], s.inflight)

    def inflight_dec(self, app: str, env: str) -> None:
        s = self._s(app, env)
        s.inflight = max(s.inflight - 1, 0)

    def set_resources(self, app: str, env: str, cpu: float | None, mem: int | None) -> None:
        g = self._gauge(self._s(app, env), now_s())
        g["cpu"], g["mem"] = cpu, mem

    def set_app_gauges(self, app: str, env: str, values: dict[str, float]) -> None:
        self._gauge(self._s(app, env), now_s())["app"].update(dict(list(values.items())[:16]))

    def record_probe(self, app: str, env: str, up: bool, now: int | None = None) -> None:
        now = now_s() if now is None else now
        tc = now - now % COARSE_S
        for b in (_slot(self._s(app, env).coarse, tc, COARSE_KEEP), self._pend(f"{app}|{env}", tc)):
            b.probes += 1
            b.probes_ok += up

    def points(self, app: str, env: str, minutes: int) -> list[dict[str, Any]]:
        s = self.series.get(f"{app}|{env}")
        if not s:
            return []
        now = now_s()
        end = now - now % FINE_S
        fine = {b.t: b for b in s.fine}
        out: list[dict[str, Any]] = []
        cpu = mem = None
        for t in range(end - minutes * 60, end + 1, FINE_S):
            b, g = fine.get(t), s.gauges.get(t)
            cpu = g["cpu"] if g and g["cpu"] is not None else cpu
            mem = g["mem"] if g and g["mem"] is not None else mem
            n = b.count if b else 0
            out.append(
                {
                    "t": t,
                    "rps": n / FINE_S,
                    "error_rate": b.errors / n if b and n else 0.0,
                    "p50": percentile(b.hist, 0.50) if b else None,
                    "p95": percentile(b.hist, 0.95) if b else None,
                    "p99": percentile(b.hist, 0.99) if b else None,
                    "cpu": cpu,
                    "mem": mem,
                }
            )
        return out

    def last_gauges(self, app: str, env: str) -> dict[str, Any] | None:
        s = self.series.get(f"{app}|{env}")
        if not s or not s.gauges:
            return None
        out: dict[str, Any] = {"inflight": s.inflight, "app": {}}
        for t in sorted(s.gauges, reverse=True):
            if s.gauges[t]["app"]:
                out["app"] = dict(s.gauges[t]["app"])
                break
        return out

    def window(self, app: str, env: str, frm: int, to: int) -> Window:
        acc = Bucket(0)
        s = self.series.get(f"{app}|{env}")
        for b in s.coarse if s else ():
            if frm <= b.t < to:
                acc.merge(b)
        avail = 1.0 - acc.errors / acc.count if acc.count else 1.0
        return Window(
            acc.count, acc.errors, avail, percentile(acc.hist, 0.95), acc.probes, acc.probes_ok, acc.hist
        )


# ---- SLOs ----------------------------------------------------------------------------


def _allowed(target_pct: float) -> float:
    return max(1.0 - target_pct / 100.0, 1e-6)


def burn_rates(m: Metrics, slo: dict[str, Any], now: int | None = None) -> tuple[float, float, int]:
    """(burn 1 h, burn ~10 min, requests in the hour)."""
    now = now_s() if now is None else now
    a = _allowed(slo["availability"])
    long_ = m.window(slo["app"], slo["env"], now - 3600, now + 1)
    short = m.window(slo["app"], slo["env"], now - 600, now + 1)

    def rate(w: Window) -> float:
        return (w.errors / w.requests) / a if w.requests else 0.0

    return rate(long_), rate(short), long_.requests


def fast_firing(burn: tuple[float, float, int]) -> bool:
    return burn[2] >= MIN_REQUESTS and burn[0] >= FAST_BURN and burn[1] >= FAST_BURN


def exhaust_label(long_burn: float, window_days: int) -> str:
    if long_burn <= 0:
        return "nunca"
    h = window_days * 24 / long_burn
    return f"~{h / 24:.0f} dias" if h >= 48 else f"~{h:.0f} h"


def slo_status(m: Metrics, slo: dict[str, Any], now: int | None = None) -> dict[str, Any]:
    now = now_s() if now is None else now
    w = m.window(slo["app"], slo["env"], now - slo["window_days"] * 86_400, now + 1)
    avail = w.availability if w.requests else (w.probes_ok / w.probes if w.probes else 1.0)
    lat = w.under(slo["latency_ms"])
    b1, b5, _ = burn_rates(m, slo, now)
    return {
        "availability": avail,
        "latency": lat,
        "requests": w.requests,
        "errors": w.errors,
        "budget_remaining": 1.0 - (1.0 - avail) / _allowed(slo["availability"]),
        "burn_1h": b1,
        "burn_5m": b5,
        "met": avail * 100 >= slo["availability"] and (lat is None or lat * 100 >= slo["latency_target"]),
    }


def daily_uptime(m: Metrics, app: str, env: str, days: int, now: int) -> list[tuple[int, float | None]]:
    """Uptime per day, oldest to today: OK probes; without probes, success rate; without anything, `None`."""
    today = now - now % 86_400
    out: list[tuple[int, float | None]] = []
    for i in range(days - 1, -1, -1):
        frm = today - i * 86_400
        w = m.window(app, env, frm, frm + 86_400)
        out.append((frm, w.probes_ok / w.probes if w.probes else (w.availability if w.requests else None)))
    return out


# ---- traces --------------------------------------------------------------------------


def new_trace_id() -> str:
    return secrets.token_hex(16)


def new_span_id() -> str:
    return secrets.token_hex(8)


class TraceStore:
    def __init__(self, cap: int = 20_000) -> None:
        self.spans: deque[dict[str, Any]] = deque(maxlen=cap)
        self.owners: dict[str, str] = {}
        self.listener: Callable[[dict[str, Any]], None] | None = None

    def claim(self, trace_id: str, app: str) -> None:
        self.owners[trace_id] = app
        while len(self.owners) > (self.spans.maxlen or 0) * 4:
            del self.owners[next(iter(self.owners))]

    def record(self, span: dict[str, Any]) -> None:
        self.spans.append(span)
        if self.listener:
            self.listener(span)

    def trace(self, trace_id: str) -> list[dict[str, Any]]:
        return sorted((s for s in self.spans if s["trace_id"] == trace_id), key=lambda s: s["start_us"])

    def slow(self, app: str | None, limit: int) -> list[dict[str, Any]]:
        counts: dict[str, list[Any]] = {}
        for s in self.spans:
            c = counts.setdefault(s["trace_id"], [0, False])
            c[0] += 1
            c[1] = c[1] or s["error"]
        roots = [
            {
                "trace_id": s["trace_id"],
                "name": s["name"],
                "app": s["attrs"].get("hub.app", ""),
                "env": s["attrs"].get("hub.env", ""),
                "start_us": s["start_us"],
                "duration_us": s["duration_us"],
                "spans": counts[s["trace_id"]][0],
                "error": counts[s["trace_id"]][1],
                "status": s["attrs"].get("http.status_code", ""),
            }
            for s in self.spans
            if s["parent_id"] is None
            and s["service"] == "hub"
            and (app is None or s["attrs"].get("hub.app") == app)
        ]
        return sorted(roots, key=lambda r: -r["duration_us"])[:limit]


@dataclass
class Telemetry:
    metrics: Metrics
    traces: TraceStore = field(default_factory=TraceStore)
    otlp: str = ""
