"""Building blocks of the public `/api/v1` API: RFC 9457 errors, the caller (`P`), call arguments, audit and the
rate limit. Shared by the HTTP routes, the MCP tools and the OAuth endpoints."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.responses import Response

_TITLES = {400: "Requisição inválida", 401: "Não autenticado", 403: "Acesso negado", 404: "Não encontrado",
           405: "Método não permitido", 409: "Conflito", 413: "Grande demais",
           415: "Tipo de conteúdo não suportado", 422: "Não processável", 429: "Muitas requisições",
           503: "Indisponível"}  # fmt: skip
_SLUGS = {400: "bad-request", 401: "unauthorized", 403: "forbidden", 404: "not-found", 409: "conflict",
          413: "too-large", 415: "unsupported-media-type", 422: "unprocessable", 423: "locked",
          429: "rate-limited", 503: "unavailable"}  # fmt: skip


class Problem(Exception):
    """RFC 9457 error (`application/problem+json`)."""

    def __init__(self, status: int, slug: str, detail: str, retry_after: int | None = None) -> None:
        super().__init__(detail)
        self.status, self.slug, self.detail, self.retry_after = status, slug, detail, retry_after

    def body(self, instance: str) -> dict[str, Any]:
        return {"type": f"/api/v1/problems/{self.slug}", "title": _TITLES.get(self.status, "Erro"),
                "status": self.status, "detail": self.detail, "instance": instance}  # fmt: skip

    def response(self, instance: str) -> Response:
        r = Response(json.dumps(self.body(instance)), self.status, media_type="application/problem+json")
        if self.retry_after is not None:
            r.headers["retry-after"] = str(self.retry_after)
        return r


@dataclass
class P:
    """Caller: session or token (with the owner's RBAC)."""

    user: dict[str, Any]
    token: dict[str, Any] | None
    perms: list[str]
    apps: list[str] | None

    @property
    def uid(self) -> str:
        return str(self.user["id"])

    @property
    def name(self) -> str:
        return str(self.user.get("name", ""))

    @property
    def session(self) -> bool:
        return self.token is None

    def has(self, *any_of: str) -> bool:
        return not any_of or any(p in self.perms for p in any_of)

    def can_app(self, app: str) -> bool:
        return (
            "acessar_aplicacoes" in self.perms
            and (self.apps is None or app in self.apps)
            and self.allows_app(app)
        )

    def token_apps(self) -> list[str] | None:
        return (list(self.token.get("apps") or []) or None) if self.token else None

    def allows_action(self, a: str) -> bool:
        return bool(self.token) and a in cast(dict[str, Any], self.token)["actions"]

    def allows_app(self, app: str) -> bool:
        apps = self.token_apps()
        return apps is None or app in apps

    @property
    def key(self) -> str:
        return f"t:{self.token['id']}" if self.token else f"u:{self.uid}"

    @property
    def rate_key(self) -> str:
        if self.token:
            owner = self.token.get("owner")
            return f"u:{owner}" if owner else f"t:{self.token['id']}"
        return f"u:{self.uid}"


def principal(request: Request) -> P | None:
    user = getattr(request.state, "user", None)
    if not user:
        return None
    return P(user, request.state.token, list(request.state.permissions), request.state.apps)


@dataclass
class Args:
    path: dict[str, str] = field(default_factory=dict)
    query: list[tuple[str, str]] = field(default_factory=list)
    body: bytes | None = None
    content_type: str | None = None

    def q(self, k: str) -> str | None:
        return next((v for n, v in self.query if n == k), None)


async def audit(request: Request, p: P, title: str, detail: str, scope: str) -> None:
    await request.app.state.admin.audit(p.name, title, detail, scope=scope, icon="settings")


# ---------------------------------------------------------------- rate limit


async def take_rate(app: FastAPI, bucket: str, key: str, per: int, window_ms: int = 60_000) -> int | None:
    """Fixed window per key; returns the seconds to wait when exceeded (`None` = allowed)."""
    now = time.time_ns() // 1_000_000
    db = getattr(app.state.auth, "db", None)
    if db is not None:
        try:
            n, retry = await db.rate_hit(bucket, key, window_ms, per, now)
            return None if n is not None else max(1, -(-int(retry) // 1000))
        except Exception:  # noqa: S110 - database down: counts in this replica
            pass
    win = now // window_ms
    _memory = cast(dict[str, tuple[int, int]], getattr(app.state, "rate_mem", None) or {})
    app.state.rate_mem = _memory
    if len(_memory) > 10_000:
        for k in [k for k, (w, _) in _memory.items() if w != win]:
            del _memory[k]
    w, n = _memory.get(f"{bucket}|{key}", (win, 0))
    if w != win:
        w, n = win, 0
    if n >= per:
        return max(1, ((win + 1) * window_ms - now) // 1000)
    _memory[f"{bucket}|{key}"] = (w, n + 1)
    return None
