"""Correções da revisão da onda 7 (sem banco): teto do corpo e resposta em streaming na
idempotência, vaga pesada que não vaza numa resposta nunca enviada."""

from __future__ import annotations

import asyncio
import gc
import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, StreamingResponse

from hub_sdk.heavy import Heavy, PermitStreamingResponse
from hub_sdk.idempotency import (
    HEADER,
    MAX_STORED_BODY,
    REPLAY_HEADER,
    Idempotency,
    IdempotencyMiddleware,
    max_body_from_env,
)


def app_with(max_body: int, calls: list[int]) -> FastAPI:
    app = FastAPI()
    app.add_middleware(IdempotencyMiddleware, idem=Idempotency.memory(), max_body=max_body)

    @app.post("/x")
    async def x(request: Request) -> Any:
        body = await request.body()
        calls.append(len(body))
        return PlainTextResponse("ok", status_code=201)

    return app


def client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


def spool_files() -> list[str]:
    return [n for n in os.listdir(tempfile.gettempdir()) if n.startswith("hub-idem-")]


def test_max_body_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUB_MAX_BODY_MB", "3")
    assert max_body_from_env() == 3 << 20
    monkeypatch.setenv("HUB_MAX_BODY_MB", "x")
    assert max_body_from_env() == 64 << 20


async def test_body_over_the_cap_is_413_before_the_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    calls: list[int] = []
    cap = 2 << 20
    async with client(app_with(cap, calls)) as c:
        h = {HEADER: "chave-teto-00001"}
        # Content-Length declarado acima do teto: 413 sem ler
        r = await c.post("/x", content=b"a" * (cap + 1), headers=h)
        assert r.status_code == 413 and "Envio muito grande" in r.text

        # chunked (sem tamanho) passando do teto: 413 e o temporário já apagado
        async def gen() -> AsyncIterator[bytes]:
            for _ in range(5):
                yield b"b" * (1 << 20)

        r = await c.post("/x", content=gen(), headers={HEADER: "chave-teto-00002"})
        assert r.status_code == 413
        assert spool_files() == []
        # dentro do teto passa (e o temporário some no fim)
        r = await c.post("/x", content=b"c" * (cap - 10), headers={HEADER: "chave-teto-00003"})
        assert r.status_code == 201
    assert calls == [cap - 10]
    assert spool_files() == []


async def test_response_is_streamed_not_buffered_and_big_ones_store_only_the_marker() -> None:
    gate = asyncio.Event()
    app = FastAPI()
    app.add_middleware(IdempotencyMiddleware, idem=Idempotency.memory(), max_body=1 << 20)
    runs: list[int] = []

    @app.post("/big")
    async def big() -> Any:
        runs.append(1)

        async def body() -> AsyncIterator[bytes]:
            yield b"x" * (MAX_STORED_BODY + 1)  # passou do teto de replay: vai direto
            await gate.wait()  # só continua depois que o cliente já recebeu o 1º pedaço
            for _ in range(4):
                yield b"y" * (MAX_STORED_BODY // 2)

        return StreamingResponse(body(), status_code=201, media_type="text/plain")

    seen: list[dict[str, Any]] = []

    async def send(msg: Any) -> None:
        seen.append(msg)
        if msg["type"] == "http.response.body" and msg.get("body"):
            gate.set()

    first = [True]

    async def receive() -> Any:
        if first[0]:
            first[0] = False
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.sleep(30)  # cliente conectado; nada mais a ler
        return {"type": "http.disconnect"}

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/big",
        "raw_path": b"/big",
        "query_string": b"",
        "headers": [(b"idempotency-key", b"chave-stream-001"), (b"content-length", b"0")],
        "http_version": "1.1",
        "scheme": "http",
        "server": ("t", 80),
        "client": ("c", 1),
        "root_path": "",
    }
    # Se a resposta fosse acumulada antes de enviar, o gate nunca abriria (timeout).
    await asyncio.wait_for(app(scope, receive, send), 5)
    sent = b"".join(m.get("body", b"") for m in seen if m["type"] == "http.response.body")
    assert len(sent) == MAX_STORED_BODY + 1 + 4 * (MAX_STORED_BODY // 2)
    async with client(app) as c:
        r = await c.post("/big", headers={HEADER: "chave-stream-001"})
    # acima do teto de replay: só a marca "concluída"; nada reexecutado
    assert r.status_code == 200 and r.headers[REPLAY_HEADER] == "true"
    assert "Operação já concluída" in r.text and runs == [1]


async def test_client_gone_mid_response_still_records_the_result() -> None:
    app = FastAPI()
    app.add_middleware(IdempotencyMiddleware, idem=Idempotency.memory(), max_body=1 << 20)
    runs: list[int] = []

    @app.post("/y")
    async def y() -> Any:
        runs.append(1)
        return PlainTextResponse("feito", status_code=201)

    async def broken_send(msg: Any) -> None:
        raise OSError("cliente foi embora")

    async def receive() -> Any:
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/y",
        "raw_path": b"/y",
        "query_string": b"",
        "headers": [(b"idempotency-key", b"chave-sumiu-0001"), (b"content-length", b"0")],
        "http_version": "1.1",
        "scheme": "http",
        "server": ("t", 80),
        "client": ("c", 1),
        "root_path": "",
    }
    await app(scope, receive, broken_send)
    async with client(app) as c:
        r = await c.post("/y", headers={HEADER: "chave-sumiu-0001"})
    assert r.status_code == 201 and r.text == "feito" and r.headers[REPLAY_HEADER] == "true"
    assert runs == [1]


async def _gen(closed: list[bool]) -> AsyncIterator[bytes]:
    try:
        yield b"a"
        yield b"b"
    finally:
        closed.append(True)


async def test_permit_released_when_response_is_never_sent() -> None:
    heavy = Heavy(1)
    permit = heavy.try_acquire()
    assert permit is not None
    resp = PermitStreamingResponse(_gen([]), permit)
    assert heavy.in_use == 1
    del resp
    gc.collect()
    assert heavy.in_use == 0 and heavy.try_acquire() is not None


async def test_permit_released_when_client_disconnects_before_start() -> None:
    heavy = Heavy(1)
    permit = heavy.try_acquire()
    assert permit is not None
    closed: list[bool] = []
    resp = PermitStreamingResponse(_gen(closed), permit)

    async def send(msg: Any) -> None:
        raise OSError("conexão fechada antes do http.response.start")

    async def receive() -> Any:
        await asyncio.sleep(10)
        return {"type": "http.disconnect"}

    with pytest.raises(Exception):  # noqa: B017 — OSError ou ClientDisconnect do Starlette
        await resp({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
    assert heavy.in_use == 0


async def test_permit_released_and_generator_closed_on_disconnect_mid_body() -> None:
    heavy = Heavy(1)
    permit = heavy.try_acquire()
    assert permit is not None
    closed: list[bool] = []
    resp = PermitStreamingResponse(_gen(closed), permit)

    async def send(msg: Any) -> None:
        if msg["type"] == "http.response.body":
            raise OSError("cliente desistiu")

    async def receive() -> Any:
        await asyncio.sleep(10)
        return {"type": "http.disconnect"}

    with pytest.raises(Exception):  # noqa: B017 — OSError ou ClientDisconnect do Starlette
        await resp({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
    assert heavy.in_use == 0 and closed == [True]
