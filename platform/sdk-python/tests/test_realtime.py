"""hub_sdk.realtime: canal de avisos (SSE/WebSocket) sem Postgres — o aviso fica no processo."""

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from hub_sdk.realtime import Canal, sse


async def test_canal_entrega_estado_inicial_aviso_e_ping(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    canal = Canal()
    await canal.iniciar()
    estado = iter(["v1", "v2"])
    fluxo = canal.mensagens(lambda: next(estado), ping=0.05)
    assert await anext(fluxo) == "v1"
    assert await anext(fluxo) is None  # nada novo dentro do ping
    canal.avisar(None)
    assert await anext(fluxo) == "v2"
    await fluxo.aclose()
    assert not canal.filas  # conexão encerrada sai do canal
    await canal.fechar()


def test_sse_formata_dados_e_ping():
    async def fonte():
        yield '{"html": "<p>1</p>"}'
        yield None

    app = FastAPI()
    app.get("/eventos")(lambda: sse(fonte()))
    r = TestClient(app).get("/eventos")
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["cache-control"] == "no-store"
    assert r.text == 'data: {"html": "<p>1</p>"}\n\n: ping\n\n'


async def test_avisar_de_outra_thread_acorda_a_conexao(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    canal = Canal()
    await canal.iniciar()
    fluxo = canal.mensagens(lambda: "x", ping=5)
    assert await anext(fluxo) == "x"
    await asyncio.to_thread(canal.avisar, None)  # rota `def` roda em thread
    assert await asyncio.wait_for(anext(fluxo), 1) == "x"
    await fluxo.aclose()
