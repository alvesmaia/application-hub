"""hub_sdk.realtime: notice channel (SSE/WebSocket) without Postgres — the notice stays in the process."""

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hub_sdk.realtime import Channel, sse


async def test_channel_delivers_initial_state_notice_and_ping(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    channel = Channel()
    await channel.start()
    state = iter(["v1", "v2"])
    stream = channel.messages(lambda: next(state), ping=0.05)
    assert await anext(stream) == "v1"
    assert await anext(stream) is None  # nothing new within the ping
    channel.notify(None)
    assert await anext(stream) == "v2"
    await stream.aclose()
    assert not channel.queues  # closed connection leaves the channel
    await channel.close()


def test_sse_formats_data_and_ping():
    async def source():
        yield '{"html": "<p>1</p>"}'
        yield None

    app = FastAPI()
    app.get("/events")(lambda: sse(source()))
    r = TestClient(app).get("/events")
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["cache-control"] == "no-store"
    assert r.text == 'data: {"html": "<p>1</p>"}\n\n: ping\n\n'


async def test_notify_from_another_thread_wakes_the_connection(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    channel = Channel()
    await channel.start()
    stream = channel.messages(lambda: "x", ping=5)
    assert await anext(stream) == "x"
    await asyncio.to_thread(channel.notify, None)  # `def` route runs in a thread
    assert await asyncio.wait_for(anext(stream), 1) == "x"
    await stream.aclose()


async def test_portuguese_names_still_work_with_deprecation_warning(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from hub_sdk.realtime import Canal, canal

    assert Canal is Channel
    assert isinstance(canal, Channel)
    c = Channel()
    with pytest.warns(DeprecationWarning, match="start"):
        await c.iniciar()
    with pytest.warns(DeprecationWarning, match="messages"):
        stream = c.mensagens(lambda: "x", ping=5)
    assert await anext(stream) == "x"
    with pytest.warns(DeprecationWarning, match="notify"):
        c.avisar(None)
    assert await asyncio.wait_for(anext(stream), 1) == "x"
    await stream.aclose()
    with pytest.warns(DeprecationWarning, match="close"):
        await c.fechar()
