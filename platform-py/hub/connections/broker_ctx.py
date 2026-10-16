"""Broker state and authorization: token, source network, binding, allowed operation and per-app slots."""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, cast

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from hub.admin.config import env_label
from hub.admin.store import AdminStore
from hub.connections.store import ConnStore
from hub.gateway.identity import USER_SIG_HEADER, verify_user
from hub.storage import StorageError


def per_app_limit() -> int:
    try:
        return max(1, int(os.getenv("HUB_BROKER_PER_APP", "8")))
    except ValueError:
        return 8


def clip(s: str, n: int) -> str:
    return "".join(c for c in s if c.isprintable())[:n]


def fail(status: int, msg: str, retry_after: int | None = None) -> JSONResponse:
    headers = {"cache-control": "no-store"} | ({"retry-after": str(retry_after)} if retry_after else {})
    return JSONResponse({"error": msg}, status_code=status, headers=headers)


def storage_fail(e: StorageError) -> JSONResponse:
    return fail(e.status, e.message, max(1, e.retry_after or 2) if e.kind == "unavailable" else None)


def who_of(headers: Any, key: bytes, app: str, env: str, now: int | None = None) -> tuple[str, str]:
    """(author, note): the user only becomes the author with a valid gateway signature for THIS app and environment."""
    raw = headers.get("x-hub-user")
    claimed = ""
    if raw:
        try:
            body = base64.urlsafe_b64decode(
                raw.strip().rstrip("=") + "=" * (-len(raw.strip().rstrip("=")) % 4)
            )
            user = json.loads(body).get("user")
            claimed = clip(user, 120) if isinstance(user, str) else ""
        except ValueError:
            claimed = ""
    if not claimed:
        return f"app:{app}", ""
    sig = headers.get(USER_SIG_HEADER)
    if raw and sig and verify_user(key, app, env, raw, sig, now):
        return claimed, ""
    return f"app:{app}", f" · usuário informado pelo app (não verificado): {claimed}"


def parse_cidrs(v: str) -> dict[str, list[ipaddress.IPv4Network]]:
    out: dict[str, list[ipaddress.IPv4Network]] = {}
    for part in v.split(","):
        app, _, cidr = part.strip().partition("=")
        try:
            net = ipaddress.IPv4Network(cidr.strip(), strict=False)
        except ValueError:
            continue
        out.setdefault(app.strip(), []).append(net)
    return out


def source_allowed(cidrs: dict[str, list[ipaddress.IPv4Network]], app: str, peer: str | None) -> bool:
    """Empty = no origin check; when configured, it closes (an app not on the list is refused)."""
    if not cidrs:
        return True
    try:
        ip = ipaddress.ip_address(peer or "")
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is None:
            return False
        ip = ip.ipv4_mapped
    return any(ip in n for n in cidrs.get(app, []))


@dataclass
class Caller:
    app: str
    env: str
    actor: str
    note: str
    conn: dict[str, Any]
    backend: (
        Any  # GraphClient | S3Client on the file routes (SQL connections do not pass the operation check)
    )
    release: Any
    admin: AdminStore

    async def record(self, title: str, detail: str) -> None:
        await self.admin.audit(
            self.actor,
            title,
            f"{detail} · {env_label(self.env)}{self.note}",
            icon="variables",
            scope=self.app,
        )

    async def ok(self, op: str, detail: str) -> None:
        await self.record(f"Armazenamento: {op}", f"{self.conn['name']} · {detail}")

    async def deny(self, op: str, detail: str, status: int, why: str) -> None:
        await self.record(
            f"Armazenamento: {op} falhou",
            f"{self.conn['name']} · {detail} · HTTP {status} · {clip(why, 300)}",
        )

    async def fail(self, op: str, detail: str, status: int, msg: str) -> JSONResponse:
        self.release()
        await self.deny(op, detail, status, msg)
        return fail(status, msg)

    async def storage_fail(self, op: str, detail: str, e: StorageError) -> JSONResponse:
        self.release()
        await self.deny(
            op, detail, e.status, f"{e.message} (provedor: {e.detail})" if e.detail else e.message
        )
        return storage_fail(e)


def bearer(request: Request) -> str:
    h = request.headers.get("authorization", "")
    return h[7:].strip() if h.startswith("Bearer ") else ""


class BrokerCtx:
    """One per broker app (`app.state.broker`); route handlers get it with `Depends(bctx)`."""

    def __init__(self, store: ConnStore, admin: AdminStore, key: bytes) -> None:
        self.store, self.admin, self.key = store, admin, key
        self.slots: dict[str, asyncio.Semaphore] = {}
        self.cidrs = parse_cidrs(os.getenv("HUB_BROKER_APP_CIDRS", ""))

    async def source_guard(self, request: Request, call_next: Any) -> Response:
        if self.cidrs and (owner := await self.store.token_owner(bearer(request))):
            peer = request.client.host if request.client else None
            if not source_allowed(self.cidrs, owner[0], peer):
                actor, note = who_of(request.headers, self.key, owner[0], owner[1])
                why = (
                    "usado fora da rede do app"
                    if owner[0] in self.cidrs
                    else "app sem rede em HUB_BROKER_APP_CIDRS"
                )
                await self.admin.audit(
                    actor,
                    "Armazenamento: acesso negado",
                    f"token de {owner[0]} {why} ({peer or ''}) · {request.method} {clip(request.url.path, 120)} · "
                    f"{env_label(owner[1])}{note}",
                    icon="variables",
                    scope=owner[0],
                )
                return fail(403, "Origem não permitida para este token.")
        return cast(Response, await call_next(request))

    async def authorize(
        self, request: Request, name: str, op: str, need: tuple[str, ...]
    ) -> Caller | JSONResponse:
        owner = await self.store.token_owner(bearer(request))
        if owner is None:
            return fail(401, "Token do broker ausente ou inválido.")
        a, env = owner
        actor, note = who_of(request.headers, self.key, a, env)

        async def record(title: str, detail: str) -> None:
            await self.admin.audit(
                actor, title, f"{detail} · {env_label(env)}{note}", icon="variables", scope=a
            )

        conn = self.store.get(name)
        if denied := await _check_conn(record, conn, name, a, env, op, need):
            return denied
        conn = cast(dict[str, Any], conn)  # `_check_conn` refused a missing one
        sem = self.slots.setdefault(f"{a}/{env}", asyncio.Semaphore(per_app_limit()))
        if sem.locked():
            await record(
                "Armazenamento: limite de operações simultâneas", f"{conn['name']} · {op} · HTTP 429"
            )
            return fail(429, "Muitas operações de armazenamento simultâneas deste app.", 2)
        await sem.acquire()
        release = _once(sem.release)
        try:
            backend = self.store.backend(conn)
        except StorageError as e:
            release()
            await record(f"Armazenamento: {op} falhou", f"{conn['name']} · HTTP 502 · {clip(e.message, 300)}")
            return fail(502, "Conexão indisponível no hub (configuração).")
        return Caller(a, env, actor, note, conn, backend, release, self.admin)


async def _check_conn(
    record: Callable[[str, str], Awaitable[None]],
    conn: dict[str, Any] | None,
    name: str,
    a: str,
    env: str,
    op: str,
    need: tuple[str, ...],
) -> JSONResponse | None:
    """The connection must be bound to this app × environment and allow one of the `need` operations."""
    if conn is None or not any(b["app"] == a and b["env"] == env for b in conn["bindings"]):
        await record("Armazenamento: acesso negado", f'{op} · conexão "{clip(name, 60)}" não vinculada a {a}')
        return fail(404, "Conexão não encontrada para este app.")
    if need and not any(o in conn["allowed_ops"] for o in need):
        await record(
            "Armazenamento: operação negada", f"{conn['name']} · {op} · {' ou '.join(need)} não liberada"
        )
        return fail(403, f'Operação "{need[0]}" não liberada nesta conexão.')
    return None


def _once(fn: Callable[[], Any]) -> Callable[[], None]:
    """The slot is released exactly once, whichever path (error, end of body) gets there first."""
    released = False

    def release() -> None:
        nonlocal released
        if not released:
            released = True
            fn()

    return release


def bctx(request: Request) -> BrokerCtx:
    """FastAPI dependency: the broker state that `build_broker` stored on the app."""
    return cast(BrokerCtx, request.app.state.broker)
