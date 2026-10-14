"""OTLP/HTTP export in JSON: only with `OTEL_EXPORTER_OTLP_ENDPOINT`.

Spans every 5 s to `{endpoint}/v1/traces`; cumulative metrics every 30 s to `{endpoint}/v1/metrics`.
No OpenTelemetry SDK (the OTLP JSON is stable). A send failure only logs a warning; the queue is bounded.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from bisect import bisect_left
from collections import deque
from typing import Any

import httpx

from hub.insight.metrics import BOUNDS, NB

log = logging.getLogger("hub.otlp")
_KIND = {"server": 2, "client": 3}


def endpoint_from_env() -> str:
    e = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip().rstrip("/")
    return e if e.startswith(("http://", "https://")) else ""


def _attr(k: str, v: Any) -> dict[str, Any]:
    return {"key": k, "value": {"stringValue": str(v)}}


def traces_body(spans: list[dict[str, Any]], hub_service: str) -> dict[str, Any]:
    by: dict[str, list[dict[str, Any]]] = {}
    for s in spans:
        svc = hub_service if s["service"] == "hub" else s["service"]
        o: dict[str, Any] = {
            "traceId": s["trace_id"],
            "spanId": s["span_id"],
            "name": s["name"],
            "kind": _KIND.get(s["kind"], 1),
            "startTimeUnixNano": str(s["start_us"] * 1000),
            "endTimeUnixNano": str((s["start_us"] + s["duration_us"]) * 1000),
            "attributes": [_attr(k, v) for k, v in s["attrs"].items()],
            "status": {"code": 2 if s["error"] else 1},
        }
        if s["parent_id"]:
            o["parentSpanId"] = s["parent_id"]
        by.setdefault(svc, []).append(o)
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": [_attr("service.name", svc)]},
                "scopeSpans": [{"scope": {"name": "hub-telemetry"}, "spans": sp}],
            }
            for svc, sp in by.items()
        ]
    }


def _total(name: str, pts: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "name": name,
        "unit": "1",
        "sum": {"aggregationTemporality": 2, "isMonotonic": True, "dataPoints": pts},
    }


class Exporter:
    def __init__(self, endpoint: str, service: str | None = None) -> None:
        self.endpoint = endpoint
        self.service = service or os.environ.get("OTEL_SERVICE_NAME", "").strip() or "hub"
        self.spans: deque[dict[str, Any]] = deque(maxlen=5000)
        self.totals: dict[str, list[Any]] = {}  # "app|env" -> [count, errors, sum_ms, hist]
        self.start_ns = time.time_ns()
        self.task: asyncio.Task[None] | None = None

    def on_span(self, span: dict[str, Any]) -> None:
        self.spans.append(span)

    def on_observe(self, app: str, env: str, status: int, ms: float) -> None:
        t = self.totals.setdefault(f"{app}|{env}", [0, 0, 0.0, [0] * NB])
        t[0] += 1
        t[1] += status >= 500
        t[2] += ms
        t[3][bisect_left(BOUNDS, ms)] += 1

    def metrics_body(self) -> dict[str, Any]:
        now = str(time.time_ns())
        reqs: list[dict[str, Any]] = []
        errs: list[dict[str, Any]] = []
        hists: list[dict[str, Any]] = []
        for k, (count, errors, total, hist) in self.totals.items():
            app, _, env = k.partition("|")
            base = {
                "attributes": [_attr("hub.app", app), _attr("hub.env", env)],
                "startTimeUnixNano": str(self.start_ns),
                "timeUnixNano": now,
            }
            reqs.append(base | {"asInt": str(count)})
            errs.append(base | {"asInt": str(errors)})
            hists.append(
                base
                | {
                    "count": str(count),
                    "sum": total,
                    "bucketCounts": [str(c) for c in hist],
                    "explicitBounds": list(BOUNDS),
                }
            )
        duration = {
            "name": "hub.http.server.duration",
            "unit": "ms",
            "histogram": {"aggregationTemporality": 2, "dataPoints": hists},
        }
        metrics = [_total("hub.http.server.requests", reqs), _total("hub.http.server.errors", errs), duration]
        return {
            "resourceMetrics": [
                {
                    "resource": {"attributes": [_attr("service.name", self.service)]},
                    "scopeMetrics": [{"scope": {"name": "hub-telemetry"}, "metrics": metrics}],
                }
            ]
        }

    async def _post(self, client: httpx.AsyncClient, path: str, body: dict[str, Any]) -> bool:
        try:
            r = await client.post(f"{self.endpoint}{path}", json=body, timeout=5)
            r.raise_for_status()
        except httpx.HTTPError as e:
            log.warning("OTLP: falha ao enviar %s: %s", path, e)
            return False
        return True

    async def flush_traces(self, client: httpx.AsyncClient) -> None:
        batch = [self.spans.popleft() for _ in range(min(len(self.spans), 512))]
        if batch and not await self._post(client, "/v1/traces", traces_body(batch, self.service)):
            self.spans.extendleft(reversed(batch))

    async def flush_metrics(self, client: httpx.AsyncClient) -> None:
        if self.totals:
            await self._post(client, "/v1/metrics", self.metrics_body())

    async def run(self, client: httpx.AsyncClient | None = None) -> None:
        own = client or httpx.AsyncClient()
        n = 0
        try:
            while True:
                await asyncio.sleep(float(os.environ.get("HUB_OTLP_TRACES_S", "5") or 5))
                await self.flush_traces(own)
                n += 1
                if n % 6 == 0:
                    await self.flush_metrics(own)
        finally:
            if client is None:
                await own.aclose()

    def start(self) -> None:
        self.task = asyncio.ensure_future(self.run())

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
