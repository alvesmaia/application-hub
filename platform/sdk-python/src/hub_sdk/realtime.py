"""Real time (SSE or WebSocket): wakes the open connections when the data changes.

With Postgres (``DATABASE_URL`` on the hub), the notice goes through ``LISTEN``/``NOTIFY`` and reaches all replicas;
without it (SQLite in ``hub dev``), it stays in the process itself. Guide: ``docs/dev/guias/tempo-real.md``.

    from hub_sdk.realtime import channel, sse

    # lifespan:  await channel.start() ... await channel.close()
    # SSE:       return sse(channel.messages(lambda: json.dumps({"html": ...})))
    # WebSocket: async for m in channel.messages(...): if m is not None: await ws.send_text(m)
    # change:   after the commit, channel.notify(engine)

The Portuguese names (``Canal``, ``canal``, ``iniciar``, ``fechar``, ``avisar``, ``mensagens``) are deprecated
aliases kept for one release.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import warnings
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from typing import Any

from fastapi.responses import StreamingResponse

PG_CHANNEL = "tempo_real"
CANAL_PG = PG_CHANNEL  # deprecated alias


def _deprecated(old: str, new: str) -> None:
    warnings.warn(f"hub_sdk.realtime: `{old}` is deprecated, use `{new}`", DeprecationWarning, stacklevel=3)


class Channel:
    def __init__(self, name: str = PG_CHANNEL) -> None:
        self.name = name
        self.queues: set[asyncio.Queue[None]] = set()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.pg: Any = None

    async def start(self) -> None:
        """In the app's lifespan. With Postgres, listens for the notice from any replica."""
        self.loop = asyncio.get_running_loop()
        url = os.environ.get("DATABASE_URL", "")
        if url.startswith(("postgres://", "postgresql://", "postgresql+psycopg://")):
            import asyncpg

            self.pg = await asyncpg.connect(url.replace("postgresql+psycopg://", "postgresql://", 1))
            await self.pg.add_listener(self.name, lambda *_: self._wake())

    async def close(self) -> None:
        if self.pg is not None:
            await self.pg.close()
            self.pg = None

    def _wake(self) -> None:
        for queue in self.queues:
            with contextlib.suppress(asyncio.QueueFull):  # a notice is already pending
                queue.put_nowait(None)

    def notify(self, engine: Any = None) -> None:
        """Call after the commit (works in ``def`` and ``async def`` routes). ``engine``: the app's SQLAlchemy;
        on Postgres the notice goes via ``pg_notify`` (all replicas)."""
        if engine is not None and engine.dialect.name == "postgresql":
            import sqlalchemy as sa

            with engine.begin() as c:
                c.execute(sa.text("SELECT pg_notify(:channel, '')"), {"channel": self.name})
        elif self.loop is not None:
            self.loop.call_soon_threadsafe(self._wake)

    async def messages(
        self, message: Callable[[], str], ping: float = 20.0
    ) -> AsyncGenerator[str | None, None]:
        """The current state on the connection, then after each notice; ``None`` = nothing new in ``ping`` seconds."""
        queue: asyncio.Queue[None] = asyncio.Queue(maxsize=1)
        self.queues.add(queue)
        try:
            yield await asyncio.to_thread(message)
            while True:
                try:
                    await asyncio.wait_for(queue.get(), ping)
                except TimeoutError:
                    yield None
                    continue
                yield await asyncio.to_thread(message)
        finally:
            self.queues.discard(queue)

    # Deprecated Portuguese names (one release).
    async def iniciar(self) -> None:
        _deprecated("iniciar", "start")
        await self.start()

    async def fechar(self) -> None:
        _deprecated("fechar", "close")
        await self.close()

    def avisar(self, engine: Any = None) -> None:
        _deprecated("avisar", "notify")
        self.notify(engine)

    def mensagens(self, mensagem: Callable[[], str], ping: float = 20.0) -> AsyncGenerator[str | None, None]:
        _deprecated("mensagens", "messages")
        return self.messages(mensagem, ping)


def sse(messages: AsyncIterator[str | None]) -> StreamingResponse:
    """``text/event-stream`` response: each message becomes ``data:``; ``None`` becomes a ping (keeps the connection open)."""

    async def stream() -> AsyncIterator[str]:
        async for m in messages:
            yield f"data: {m}\n\n" if m is not None else ": ping\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})


channel = Channel()
Canal = Channel  # deprecated alias
canal = channel  # deprecated alias
