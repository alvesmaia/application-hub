"""Traces do app (onda 8) — mesmo contrato do SDK Rust (``hub_sdk::telemetry``).

- :class:`TraceMiddleware` (ASGI puro, funciona com respostas em streaming): lê o
  ``traceparent`` que o gateway injeta (o do navegador é descartado pelo hub), abre o span
  ``server`` do app como filho do span do hub e guarda o contexto num ``ContextVar``.
- :func:`db` — ``async with telemetry.db("SELECT contratos"):`` cria o span ``client`` do
  banco, filho do atual; :func:`sql_comment` devolve ``/*traceparent='…'*/`` (sqlcommenter)
  para prefixar o SQL (aparece no ``pg_stat_activity``/logs do Postgres).
- :class:`Telemetry` guarda os spans num buffer limitado; o hub os busca por
  ``GET /{app}/_hub/telemetry?after=`` (os apps não alcançam o plano de controle) junto com
  os medidores de saturação (:meth:`Telemetry.set_gauges`).

Sem dependências novas (não usa o SDK do OpenTelemetry: a imagem fixa tudo por hash); o
formato dos spans é o do hub (``hub_telemetry::Span``), que o reexporta via OTLP.
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
            if after > self._seq:  # app reiniciou: sequência zerada
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


# (contexto atual, telemetria) — disponível durante a requisição.
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
    """Span ``client`` de banco (``db.system=postgresql``)."""
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
    """``http.target`` seguro (mesmo algoritmo de ``hub_telemetry::redact::target``):
    sem query/fragmento; ids, e-mails, tokens e segmentos após palavras sensíveis
    trocados por marcadores."""
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
    """Span ``server`` por requisição (filho do ``traceparent`` do hub)."""

    def __init__(
        self, app: Callable[[Scope, Receive, Send], Awaitable[None]], telemetry: Callable[[], Telemetry]
    ):
        self.app = app
        self.telemetry = telemetry

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        path = str(scope.get("path", ""))
        segs = path.split("/")
        if len(segs) > 2 and segs[2] == "_hub":
            await self.app(scope, receive, send)
            return
        tel = self.telemetry()
        tp = None
        for k, v in scope.get("headers", []):
            if k == b"traceparent":
                tp = v.decode("latin-1")
                break
        parent = TraceContext.parse(tp)
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
            code = status["code"]
            tel.record(
                tel.span_dict(
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
            )
