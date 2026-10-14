"""Gateway instrumentation and background tasks: metrics, traces, probes, SLOs, analytics."""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
from fastapi import Request, Response

from hub.admin.store import AdminStore
from hub.gateway.registry import Registry
from hub.insight import flags as fl
from hub.insight.analytics import Analytics
from hub.insight.metrics import (
    Metrics,
    Telemetry,
    burn_rates,
    enabled,
    exhaust_label,
    fast_firing,
    new_span_id,
    new_trace_id,
)
from hub.insight.ops import InsightOps
from hub.insight.otlp import Exporter, endpoint_from_env
from hub.insight.usage import PLATFORM, Usage
from hub.vault import Vault

_SENSITIVE = frozenset(
    [
        "reset",
        "redefinir",
        "token",
        "tokens",
        "invite",
        "invites",
        "convite",
        "convites",
        "verify",
        "verificar",
        "confirm",
        "confirmar",
        "signed",
        "magic",
        "share",
        "secret",
        "key",
        "keys",
        "session",
        "sessions",
        "password",
        "senha",
    ]
)
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def redact_target(path: str) -> str:
    """`http.target` without the query, with ids, emails, tokens and sensitive segments replaced by markers."""
    prev = ""
    out: list[str] = []
    for s in path.split("?")[0].split("#")[0].split("/"):
        if not s:
            out.append(s)
        elif prev.lower() in _SENSITIVE:
            out.append(":redacted")
        elif "@" in s or "%40" in s.lower():
            out.append(":email")
        elif (s.isascii() and s.isdigit()) or _UUID.fullmatch(s):
            out.append(":id")
        elif len(s) >= 32 or (len(s) >= 16 and any(c.isalpha() for c in s) and any(c.isdigit() for c in s)):
            out.append(":token")
        else:
            out.append(s)
        prev = s
    return "/".join(out)[:200]


def _valid_hex(s: Any, n: int) -> bool:
    return isinstance(s, str) and len(s) == n and all(c in "0123456789abcdef" for c in s)


class Insight:
    def __init__(
        self, data_dir: Path, vault: Vault | None, admin: AdminStore, registry: Registry, ux: Any
    ) -> None:
        self.tel = Telemetry(Metrics(data_dir))
        self.otlp = Exporter(ep) if (ep := endpoint_from_env()) and enabled() else None
        if self.otlp:
            self.tel.otlp = ep
            self.tel.traces.listener = self.otlp.on_span
            self.tel.metrics.listener = self.otlp.on_observe
        self.analytics = Analytics(data_dir, vault, os.environ.get("HUB_ANALYTICS") != "0")
        self.analytics.ux = ux
        self.usage = Usage(data_dir, os.environ.get("HUB_USAGE") != "0")
        self.admin, self.registry = admin, registry
        self.ops = InsightOps(admin)
        self.tasks: list[asyncio.Task[None]] = []
        self.firing: dict[str, bool] = {}
        self.is_leader: Callable[[], bool] = lambda: True  # only the leader opens/closes incidents and alerts
        self.on_alert: Callable[[str, str, str, str, str], Awaitable[None]] | None = None
        self.hub_jobs: Callable[[], Awaitable[int]] | None = (
            None  # hub jobs in progress, counted across every replica
        )

    # ---- gateway -----------------------------------------------------------------------------------------
    def upstream_headers(
        self, request: Request, app_id: str, user: str, roles: list[str]
    ) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        if (ctx := getattr(request.state, "trace", None)) and enabled():
            out.append(("traceparent", f"00-{ctx[0]}-{ctx[1]}-01"))
        value = fl.header_value(getattr(request.state, "flag_objs", []), app_id, "producao", user, roles)
        return [*out, (fl.HEADER, value)] if value else out

    async def observed(
        self, request: Request, app_id: str, call: Callable[[], Awaitable[Response]]
    ) -> Response:
        """Wraps `/_frag`: root span + RED metrics + a line in the logs console."""
        known = self.registry.get(app_id) is not None
        who = getattr(request.state, "user", {"name": "anônimo", "id": ""})
        if known:
            request.state.flag_objs = await self.ops.flag_objects()
        ctx = (new_trace_id(), new_span_id())
        request.state.trace = ctx
        rest = request.url.path.removeprefix("/_frag")
        t0, t0_us = time.monotonic(), time.time_ns() // 1000
        on = enabled() and known
        if on:
            self.tel.metrics.inflight_inc(app_id, "producao")
            self.tel.traces.claim(ctx[0], app_id)
        try:
            resp = await call()
        finally:
            if on:
                self.tel.metrics.inflight_dec(app_id, "producao")
        ms = (time.monotonic() - t0) * 1000
        if on:
            self.tel.metrics.observe(app_id, "producao", resp.status_code, ms)
            self.tel.traces.record(
                {
                    "trace_id": ctx[0],
                    "span_id": ctx[1],
                    "parent_id": None,
                    "name": f"{request.method} /{app_id}",
                    "service": "hub",
                    "kind": "server",
                    "start_us": t0_us,
                    "duration_us": int(ms * 1000),
                    "error": resp.status_code >= 500,
                    "attrs": {
                        "http.method": request.method,
                        "http.target": redact_target(rest),
                        "http.status_code": str(resp.status_code),
                        "hub.app": app_id,
                        "hub.env": "producao",
                        "enduser.id": str(who.get("id", ""))[:100],
                    },
                }
            )
        self.admin.logs.gateway(
            request.method, rest, resp.status_code, int(ms), app_id if known else "platform", who["name"]
        )
        return resp

    def span_for_api(self, request: Request, status: int, started: float, started_us: int) -> None:
        """Control-plane actions (`/api/*` that change something) become hub spans."""
        path = request.url.path
        area = path.split("/")[2] if path.count("/") >= 2 else ""
        target = redact_target(path)
        self.tel.traces.record(
            {
                "trace_id": new_trace_id(),
                "span_id": new_span_id(),
                "parent_id": None,
                "name": f"{area}: {request.method} {target[:80]}",
                "service": "hub",
                "kind": "server",
                "start_us": started_us,
                "duration_us": int((time.monotonic() - started) * 1e6),
                "error": status >= 500,
                "attrs": {
                    "http.method": request.method,
                    "http.target": target,
                    "http.status_code": str(status),
                    "hub.area": area,
                    "hub.app": "platform",
                },
            }
        )

    # ---- analytics ---------------------------------------------------------------------------------------
    def track(self, request: Request, status: int, can_app: Callable[[str], bool]) -> None:
        if not 200 <= status < 400 or (user := getattr(request.state, "user", None)) is None:
            return
        uid, segs, rec = user["id"], request.url.path.strip("/").split("/"), self.analytics.record
        if request.method == "GET":
            if segs[0] == "_frag":
                sec = segs[2] if len(segs) > 2 else ""
                ok = sec and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", sec) and not sec.startswith(("_", "e2e"))
                if request.headers.get("x-hub-nav") and ok and can_app(segs[1]):
                    rec(uid, "section", segs[1], sec)
                    self.usage.record(uid, segs[1], "section")
            elif segs[0] in ("api", "assets", "account"):
                return
            elif (key := page_key(segs)) is not None:
                rec(uid, "page", key)
                self.usage.record(uid, PLATFORM, "page")
            elif (
                segs[0] == "apps" and 2 <= len(segs) <= 3 and self.registry.get(segs[1]) and can_app(segs[1])
            ):
                rec(uid, "open", segs[1])
                self.usage.record(uid, segs[1], "open")
        elif segs[0] == "_frag" and len(segs) > 1:
            rec(uid, "action", segs[1])
            self.usage.record(uid, segs[1], "action")

    # ---- tarefas ---------------------------------------------------------------------------------------
    def start(self, client: httpx.AsyncClient) -> None:
        loops: list[Any] = [self._housekeeping, lambda: self._app_channel(client)]
        if enabled():
            loops += [lambda: self._probes(client), self._slo_alerts]
        self.tasks = [asyncio.ensure_future(fn()) for fn in loops]
        if self.otlp:
            self.otlp.start()

    async def stop(self) -> None:
        if self.otlp:
            await self.otlp.stop()
        for t in self.tasks:
            t.cancel()
        for t in self.tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await t
        self.tel.metrics.save()
        self.analytics.flush()
        self.usage.flush(self.analytics.retention)

    async def _housekeeping(self) -> None:
        while True:
            await asyncio.sleep(30)
            self.analytics.flush()
            self.usage.flush(self.analytics.retention)
            self.tel.metrics.save()

    def targets(self) -> list[tuple[str, str]]:
        return [(e.id, e.upstream.rstrip("/")) for e in self.registry.entries()]

    async def _probes(self, client: httpx.AsyncClient) -> None:
        """Every minute: `GET {upstream}/{id}` (200 within 2 s) feeds the availability bars."""
        while True:
            await asyncio.sleep(60)
            for app_id, up in self.targets():
                try:
                    ok = (await client.get(f"{up}/{app_id}", timeout=2)).is_success
                except httpx.HTTPError:
                    ok = False
                self.tel.metrics.record_probe(app_id, "producao", ok)

    async def _slo_alerts(self) -> None:
        """Fast burn of the error budget (1 h and ~10 min, 14.4x) opens an automatic incident."""
        while True:
            await asyncio.sleep(60)
            if not self.is_leader():
                continue
            for slo in (await self.admin.cfg.read()).get("slos", []):
                await self.check_slo(slo)

    async def check_slo(self, slo: dict[str, Any]) -> None:
        k = f"{slo['app']}|{slo['env']}"
        burn = burn_rates(self.tel.metrics, slo)
        now_firing = fast_firing(burn)
        title = f"{self._short(slo['app'])}: orçamento de erro queimando rápido"
        if now_firing and not self.firing.get(k):
            detail = (
                f"Taxa de queima {burn[0]:.1f}× (1 h) e {burn[1]:.1f}× (5 min) no ambiente {slo['env']} — "
                f"no ritmo atual o orçamento de {slo['window_days']} dias acaba em {exhaust_label(burn[0], slo['window_days'])}."
            ).replace(".", ",", 2)
            await self.ops.open_auto_incident(slo["app"], slo["env"], title, "minor", detail)
            if self.on_alert:
                await self.on_alert("warning", title, detail, slo["app"], "/admin/health")
        elif not now_firing and self.firing.get(k):
            await self.ops.resolve_auto_incident(
                slo["app"], slo["env"], title, "Taxa de erro voltou ao normal."
            )
        self.firing[k] = now_firing

    def _short(self, app_id: str) -> str:
        e = self.registry.get(app_id)
        return (e.display.get("short") or e.name) if e else "Plataforma"

    async def _app_channel(self, client: httpx.AsyncClient) -> None:
        """Fetches new spans and meters from each app (`/{id}/_hub/telemetry`, internal token)."""
        every = max(int(os.environ.get("HUB_TELEMETRY_POLL_MS", "2000") or 2000), 200) / 1000
        cursor: dict[str, int] = {}
        pushed: dict[str, tuple[int, float]] = {}
        while True:
            await asyncio.sleep(every)
            flags, version = await self.ops.flags()
            objs = [fl.Flag.model_validate(f) for f in flags]
            for app_id, up in self.targets():
                if not (token := self.admin.tokens.get(app_id)):
                    continue
                await self._push_flags(client, app_id, up, token, objs, version, pushed)
                if not enabled():
                    continue
                try:
                    r = await client.get(
                        f"{up}/{app_id}/_hub/telemetry?after={cursor.get(app_id, 0)}",
                        headers={"x-hub-internal-token": token},
                        timeout=3,
                    )
                    if r.is_success:
                        body = r.json()
                        self.ingest(app_id, "producao", body)
                        cursor[app_id] = int(body.get("next", cursor.get(app_id, 0)))
                except (httpx.HTTPError, ValueError, TypeError):
                    continue

    @staticmethod
    async def _push_flags(
        client: httpx.AsyncClient,
        app_id: str,
        up: str,
        token: str,
        flags: list[fl.Flag],
        version: int,
        pushed: dict[str, tuple[int, float]],
    ) -> None:
        """Flag definitions in the container (tasks without a user): on every change and every 30 s."""
        last = pushed.get(app_id)
        if last and last[0] == version and time.monotonic() - last[1] < 30:
            return
        body = {"version": version, "env": "producao", "flags": fl.for_push(flags, app_id, "producao")}
        try:
            r = await client.post(
                f"{up}/{app_id}/_hub/flags", json=body, headers={"x-hub-internal-token": token}, timeout=3
            )
        except httpx.HTTPError:
            return
        if r.is_success or r.status_code == 404:
            pushed[app_id] = (version, time.monotonic())

    def ingest(self, app: str, env: str, payload: dict[str, Any]) -> int:
        """Stores validated spans (service = the app) and meters; never trusts the app to speak for another."""
        n = 0
        for s in (payload.get("spans") or [])[:2000]:
            if not self._sane(s) or self.tel.traces.owners.setdefault(s["trace_id"], app) != app:
                continue
            attrs = {
                k: str(v) for k, v in s.get("attrs", {}).items() if not k.startswith(("hub.", "enduser."))
            }
            for k in ("http.target", "http.url"):
                if k in attrs:
                    attrs[k] = redact_target(attrs[k])
            attrs |= {"hub.app": app, "hub.env": env}
            self.tel.traces.record(
                {
                    "trace_id": s["trace_id"],
                    "span_id": s["span_id"],
                    "parent_id": s.get("parent_id"),
                    "name": s["name"],
                    "service": app,
                    "kind": s.get("kind", "internal"),
                    "start_us": int(s["start_us"]),
                    "duration_us": int(s["duration_us"]),
                    "error": bool(s.get("error")),
                    "attrs": attrs,
                }
            )
            n += 1
        if isinstance(g := payload.get("gauges"), dict):
            self.tel.metrics.set_app_gauges(
                app, env, {k: float(v) for k, v in g.items() if isinstance(v, int | float) and len(k) <= 40}
            )
        return n

    @staticmethod
    def _sane(s: Any) -> bool:
        try:
            attrs = s.get("attrs", {})
            return (
                _valid_hex(s["trace_id"], 32)
                and _valid_hex(s["span_id"], 16)
                and (s.get("parent_id") is None or _valid_hex(s["parent_id"], 16))
                and len(s["name"]) <= 200
                and len(attrs) <= 32
                and all(len(str(k)) <= 64 and len(str(v)) <= 512 for k, v in attrs.items())
                and 0 <= int(s["duration_us"]) < 24 * 3600 * 1_000_000
                and int(s["start_us"]) >= 0
            )
        except (KeyError, TypeError, ValueError, AttributeError):
            return False


def page_key(segs: list[str]) -> str | None:
    """Hub page normalized for analytics (no ids or query)."""
    if segs == [""]:
        return "/"
    if len(segs) == 1 and segs[0] in ("applications", "inbox", "search", "status", "admin"):
        return "/" + segs[0]
    if len(segs) >= 2 and segs[0] == "admin" and re.fullmatch(r"[A-Za-z0-9-]+", segs[1]):
        return f"/admin/{segs[1]}"
    return None
