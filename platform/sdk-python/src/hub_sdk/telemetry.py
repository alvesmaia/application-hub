"""App traces (wave 8).

- :class:`TraceMiddleware` (pure ASGI, works with streaming responses): reads the
  ``traceparent`` the gateway injects (the browser's is discarded by the hub), opens the
  ``server`` span of the app as a child of the hub's span and stores the context in a ``ContextVar``.
- :func:`db` — ``async with telemetry.db("SELECT contratos"):`` creates the ``client`` span of the
  database, child of the current one; :func:`sql_comment` returns ``/*traceparent='…'*/`` (sqlcommenter)
  to prefix the SQL (it shows up in ``pg_stat_activity``/the Postgres logs).
- :class:`Telemetry` keeps the spans in a bounded buffer; the hub fetches them via
  ``GET /{app}/_hub/telemetry?after=`` (apps cannot reach the control plane), along with
  the saturation gauges (:meth:`Telemetry.set_gauges`).

No new dependencies (does not use the OpenTelemetry SDK: the image pins everything by hash); the
span format is the hub's (``hub_telemetry::Span``), which re-exports it via OTLP.
"""

from __future__ import annotations

import contextlib
import re
import secrets
import threading
import time
from collections import deque
from collections.abc import AsyncGenerator, Awaitable, Callable, MutableMapping
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

CAP = 5000
_HEX32 = re.compile(r"^[0-9a-f]{32}$")
_HEX16 = re.compile(r"^[0-9a-f]{16}$")


@dataclass(frozen=True)
class TraceContext:
    trace_id: str
    span_id: str
    sampled: bool = True

    @staticmethod
    def parse(v: str | None) -> TraceContext | None:
        if v is None or not v:
            return None
        p = v.strip().split("-")
        if len(p) != 4 or p[0] != "00" or not _HEX32.match(p[1]) or not _HEX16.match(p[2]):
            return None
        if not re.match(r"^[0-9a-f]{2}$", p[3]) or set(p[1]) == {"0"} or set(p[2]) == {"0"}:
            return None
        return TraceContext(p[1], p[2], int(p[3], 16) & 1 == 1)

    @staticmethod
    def root() -> TraceContext:
        return TraceContext(_nonzero_hex(16), _nonzero_hex(8))

    def child(self) -> TraceContext:
        return TraceContext(self.trace_id, _nonzero_hex(8), self.sampled)

    def header(self) -> str:
        return f"00-{self.trace_id}-{self.span_id}-{'01' if self.sampled else '00'}"


def _nonzero_hex(nbytes: int) -> str:
    while True:
        h = secrets.token_hex(nbytes)
        if set(h) != {"0"}:
            return h


def now_us() -> int:
    return time.time_ns() // 1000


class Telemetry:
    def __init__(self, service: str) -> None:
        self.service = service
        self._lock = threading.Lock()
        self._seq = 0
        self._spans: deque[tuple[int, dict[str, Any]]] = deque(maxlen=CAP)
        self._gauges: Callable[[], dict[str, float]] | None = None

    def set_gauges(self, f: Callable[[], dict[str, float]]) -> None:
        self._gauges = f

    def gauges(self) -> dict[str, float]:
        try:
            return dict(self._gauges()) if self._gauges else {}
        except Exception:
            return {}

    def record(self, span: dict[str, Any]) -> None:
        with self._lock:
            self._seq += 1
            self._spans.append((self._seq, span))

    def pending(self, after: int, limit: int) -> tuple[list[dict[str, Any]], int]:
        with self._lock:
            if after > self._seq:  # app restarted: sequence reset
                after = 0
            out = [(n, s) for n, s in self._spans if n > after][:limit]
        nxt = out[-1][0] if out else after
        return [s for _, s in out], nxt

    def span_dict(
        self,
        ctx: TraceContext,
        parent: str | None,
        name: str,
        kind: str,
        start: int,
        dur: int,
        error: bool,
        attrs: dict[str, str],
    ) -> dict[str, Any]:
        d: dict[str, Any] = {
            "trace_id": ctx.trace_id,
            "span_id": ctx.span_id,
            "name": name[:200],
            "service": self.service,
            "kind": kind,
            "start_us": start,
            "duration_us": max(dur, 0),
            "error": error,
            "attrs": attrs,
        }
        if parent is not None and parent:
            d["parent_id"] = parent
        return d


# (current context, telemetry) — available during the request.
_CURRENT: ContextVar[tuple[TraceContext, Telemetry] | None] = ContextVar("hub_trace", default=None)


def current() -> TraceContext | None:
    c = _CURRENT.get()
    return c[0] if c else None


def sql_comment() -> str:
    c = current()
    return f"/*traceparent='{c.header()}'*/ " if c else ""


@contextlib.asynccontextmanager
async def span(
    name: str, kind: str = "internal", attrs: dict[str, str] | None = None
) -> AsyncGenerator[None]:
    cur = _CURRENT.get()
    if cur is None:
        yield
        return
    parent, tel = cur
    ctx = parent.child()
    token = _CURRENT.set((ctx, tel))
    start = now_us()
    err = False
    try:
        yield
    except BaseException:
        err = True
        raise
    finally:
        _CURRENT.reset(token)
        tel.record(tel.span_dict(ctx, parent.span_id, name, kind, start, now_us() - start, err, attrs or {}))


def db(op: str) -> contextlib.AbstractAsyncContextManager[None]:
    """DB ``client`` span (``db.system=postgresql``)."""
    return span(f"db {op}", "client", {"db.system": "postgresql", "db.operation": op})


_SENSITIVE_PREV = frozenset(
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
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _segment(prev: str, s: str) -> str:
    if not s:
        return ""
    if prev.lower() in _SENSITIVE_PREV:
        return ":redacted"
    if "@" in s or "%40" in s.lower():
        return ":email"
    if (s.isascii() and s.isdigit()) or _UUID.match(s):
        return ":id"
    letters = sum(c.isascii() and c.isalpha() for c in s)
    digits = sum(c.isascii() and c.isdigit() for c in s)
    if len(s) >= 32 or (len(s) >= 16 and letters and digits):
        return ":token"
    return s


def redact_target(path: str) -> str:
    """Safe ``http.target`` (same algorithm as ``hub_telemetry::redact::target``):
    no query/fragment; ids, emails, tokens and segments after sensitive words
    replaced by markers."""
    path = str(re.split(r"[?#]", path, maxsplit=1)[0])
    out: list[str] = []
    prev = ""
    for s in path.split("/"):
        out.append(_segment(prev, s))
        prev = s
    return "/".join(out)[:200]


def _route_name(path: str) -> str:
    parts = redact_target(path).split("/")[:4]
    return "/".join("{id}" if p == ":id" else p for p in parts)


Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]


class TraceMiddleware:
    """``server`` span per request (child of the hub's ``traceparent``)."""

    def __init__(
        self, app: Callable[[Scope, Receive, Send], Awaitable[None]], telemetry: Callable[[], Telemetry]
    ):
        self.app = app
        self.telemetry = telemetry

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = str(scope.get("path", ""))
        if not _traced(scope, path):
            await self.app(scope, receive, send)
            return
        tel = self.telemetry()
        parent = TraceContext.parse(_traceparent(scope))
        ctx = parent.child() if parent else TraceContext.root()
        status = {"code": 500}

        async def send_wrapper(msg: MutableMapping[str, Any]) -> None:
            if msg.get("type") == "http.response.start":
                status["code"] = int(msg.get("status", 500))
            await send(msg)

        token = _CURRENT.set((ctx, tel))
        start = now_us()
        method = scope.get("method", "GET")
        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            _CURRENT.reset(token)
            tel.record(_server_span(tel, ctx, parent, method, path, start, status["code"]))


def _server_span(
    tel: Telemetry,
    ctx: TraceContext,
    parent: TraceContext | None,
    method: str,
    path: str,
    start: int,
    code: int,
) -> dict[str, Any]:
    return tel.span_dict(
        ctx,
        parent.span_id if parent else None,
        f"{method} {_route_name(path)}",
        "server",
        start,
        now_us() - start,
        code >= 500,
        {
            "http.method": method,
            "http.target": redact_target(path),
            "http.status_code": str(code),
        },
    )


def _traced(scope: Scope, path: str) -> bool:
    if scope.get("type") != "http":
        return False
    segs = path.split("/")
    return not (len(segs) > 2 and segs[2] == "_hub")


def _traceparent(scope: Scope) -> str | None:
    for k, v in scope.get("headers", []):
        if k == b"traceparent":
            return str(v.decode("latin-1"))
    return None
