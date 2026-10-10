"""Middleware de idempotência (modo em memória) e endpoints internos, por HTTP."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse

from hub_sdk import internal
from hub_sdk.idempotency import (
    HEADER,
    REPLAY_HEADER,
    USER_HEADER,
    Idempotency,
    IdempotencyMiddleware,
    idem_ctx,
)
from hub_sdk.outbox import Outbox, audit

TOKEN = "0123456789abcdef0123"


def idem_app(idem: Idempotency, calls: list[bytes], delay: float = 0.0) -> FastAPI:
    app = FastAPI()
    app.add_middleware(IdempotencyMiddleware, idem=idem)

    @app.post("/x")
    async def x(request: Request) -> Any:
        body = await request.body()
        if HEADER in request.headers:
            assert idem_ctx(request) is not None
        if delay:
            await asyncio.sleep(delay)
        calls.append(body)
        return PlainTextResponse(body, status_code=201)

    @app.post("/fail")
    async def fail() -> Any:
        return PlainTextResponse("x", status_code=503)

    return app


def client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def chunks(*parts: bytes) -> AsyncIterator[bytes]:
    for p in parts:
        yield p


async def test_replay_mismatch_and_user_scope_with_chunked_bodies() -> None:
    calls: list[bytes] = []
    async with client(idem_app(Idempotency.memory(), calls)) as c:

        async def send(user: str, *parts: bytes) -> tuple[int, bool, str]:
            r = await c.post(
                "/x", content=chunks(*parts), headers={HEADER: "chave-compartilhada", USER_HEADER: user}
            )
            return r.status_code, bool(r.headers.get(REPLAY_HEADER) == "true"), r.text

        assert await send("ana", b"a=1&", b"b=2") == (201, False, "a=1&b=2")
        # mesmo conteúdo, pedaços diferentes → repetição
        assert await send("ana", b"a=", b"1&b=2") == (201, True, "a=1&b=2")
        # conteúdo diferente só no fim → 422
        assert (await send("ana", b"a=1&", b"b=3"))[0] == 422
        # outro usuário com a mesma chave nunca recebe a resposta de ana
        assert await send("bia", b"a=1&", b"b=2") == (201, False, "a=1&b=2")
    assert len(calls) == 2


async def test_large_body_is_spooled_hashed_and_replayed() -> None:
    calls: list[bytes] = []
    big = b"z" * (3 << 20)
    async with client(idem_app(Idempotency.memory(), calls)) as c:
        h = {HEADER: "chave-grande-0001"}
        r1 = await c.post("/x", content=big, headers=h)
        assert r1.status_code == 201 and len(r1.content) == len(big)
        r2 = await c.post("/x", content=big, headers=h)
        # resposta acima de 256 KiB não é guardada: a repetição recebe 200 "já concluída"
        assert r2.status_code == 200 and r2.headers[REPLAY_HEADER] == "true"
        assert "Operação já concluída" in r2.text
        other = bytearray(big)
        other[-1] = ord("y")
        assert (await c.post("/x", content=bytes(other), headers=h)).status_code == 422
    assert calls == [big]


async def test_parallel_same_key_waits_and_replays_or_409() -> None:
    calls: list[bytes] = []
    idem = Idempotency(None, wait=0.2)
    async with client(idem_app(idem, calls, delay=0.6)) as c:
        h = {HEADER: "paralela-0001"}
        a, b = await asyncio.gather(
            c.post("/x", content=b"p", headers=h), c.post("/x", content=b"p", headers=h)
        )
        statuses = sorted([a.status_code, b.status_code])
        assert statuses == [201, 409]
        busy = a if a.status_code == 409 else b
        assert busy.headers["retry-after"] == "2"
    assert calls == [b"p"]


async def test_invalid_key_and_non_storable_status() -> None:
    calls: list[bytes] = []
    async with client(idem_app(Idempotency.memory(), calls)) as c:
        assert (await c.post("/x", content=b"a", headers={HEADER: "curta"})).status_code == 400
        h = {HEADER: "falha-transitoria-1"}
        assert (await c.post("/fail", headers=h)).status_code == 503
        r = await c.post("/fail", headers=h)
        assert r.status_code == 503 and REPLAY_HEADER not in r.headers  # 5xx não é guardado
        # sem chave: passa direto, sem idempotência
        assert (await c.post("/x", content=b"k")).status_code == 201


def internal_app(token: str | None, outbox: Outbox) -> FastAPI:
    app = FastAPI()
    app.include_router(internal.router("demo", token, outbox, None))
    return app


async def test_internal_requires_configured_token_and_clamps() -> None:
    ob = Outbox.memory()
    for i in range(internal.MAX_PAGE + 20):
        await ob.emit(audit("u", "a", str(i)))
    async with client(internal_app(None, ob)) as c:
        assert (await c.get("/demo/_hub/outbox", headers={internal.TOKEN_HEADER: "x"})).status_code == 403
    async with client(internal_app(TOKEN, ob)) as c:
        assert (await c.get("/demo/_hub/outbox")).status_code == 401
        assert (
            await c.get("/demo/_hub/outbox", headers={internal.TOKEN_HEADER: "errado-errado-errado"})
        ).status_code == 401
        h = {internal.TOKEN_HEADER: TOKEN}
        r = await c.get("/demo/_hub/outbox?limit=1000000", headers=h)
        assert r.status_code == 200 and len(r.json()["events"]) == internal.MAX_PAGE
        ev = r.json()["events"][0]
        assert set(ev) == {"id", "event_id", "kind", "user", "action", "target", "detail", "at"}
        r = await c.post("/demo/_hub/outbox/ack", json={"ids": ["x"] * 1001}, headers=h)
        assert r.status_code == 400
        r = await c.post("/demo/_hub/outbox/ack", json={"ids": [ev["event_id"]]}, headers=h)
        assert r.json() == {"acked": 1}
        r = await c.get("/demo/_hub/jobs", headers=h)
        assert r.json() == {"jobs": [], "enabled": False}
        assert (await c.post("/demo/_hub/jobs/job-1/retry", headers=h)).status_code == 404
