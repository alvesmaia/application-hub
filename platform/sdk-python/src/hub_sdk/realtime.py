"""Real time (SSE or WebSocket): wakes the open connections when the data changes.

With Postgres (``DATABASE_URL`` on the hub), the notice goes through ``LISTEN``/``NOTIFY`` and reaches all replicas;
without it (SQLite in ``hub dev``), it stays in the process itself. Guide: ``docs/dev/guias/tempo-real.md``.

    from hub_sdk.realtime import canal, sse

    # lifespan:  await canal.iniciar() ... await canal.fechar()
    # SSE:       return sse(canal.mensagens(lambda: json.dumps({"html": ...})))
    # WebSocket: async for m in canal.mensagens(...): if m is not None: await ws.send_text(m)
    # change:   after the commit, canal.avisar(engine)
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from typing import Any

from fastapi.responses import StreamingResponse

CANAL_PG = "tempo_real"


class Canal:
    def __init__(self, nome: str = CANAL_PG) -> None:
        self.nome = nome
        self.filas: set[asyncio.Queue[None]] = set()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.pg: Any = None

    async def iniciar(self) -> None:
        """In the app's lifespan. With Postgres, listens for the notice from any replica."""
        self.loop = asyncio.get_running_loop()
        url = os.environ.get("DATABASE_URL", "")
        if url.startswith(("postgres://", "postgresql://", "postgresql+psycopg://")):
            import asyncpg

            self.pg = await asyncpg.connect(url.replace("postgresql+psycopg://", "postgresql://", 1))
            await self.pg.add_listener(self.nome, lambda *_: self._acordar())

    async def fechar(self) -> None:
        if self.pg is not None:
            await self.pg.close()
            self.pg = None

    def _acordar(self) -> None:
        for fila in self.filas:
            with contextlib.suppress(asyncio.QueueFull):  # a notice is already pending
                fila.put_nowait(None)

    def avisar(self, engine: Any = None) -> None:
        """Call after the commit (works in ``def`` and ``async def`` routes). ``engine``: the app's SQLAlchemy;
        on Postgres the notice goes via ``pg_notify`` (all replicas)."""
        if engine is not None and engine.dialect.name == "postgresql":
            import sqlalchemy as sa

            with engine.begin() as c:
                c.execute(sa.text("SELECT pg_notify(:canal, '')"), {"canal": self.nome})
        elif self.loop is not None:
            self.loop.call_soon_threadsafe(self._acordar)

    async def mensagens(
        self, mensagem: Callable[[], str], ping: float = 20.0
    ) -> AsyncGenerator[str | None, None]:
        """The current state on the connection, then after each notice; ``None`` = nothing new in ``ping`` seconds."""
        fila: asyncio.Queue[None] = asyncio.Queue(maxsize=1)
        self.filas.add(fila)
        try:
            yield await asyncio.to_thread(mensagem)
            while True:
                try:
                    await asyncio.wait_for(fila.get(), ping)
                except TimeoutError:
                    yield None
                    continue
                yield await asyncio.to_thread(mensagem)
        finally:
            self.filas.discard(fila)


def sse(mensagens: AsyncIterator[str | None]) -> StreamingResponse:
    """``text/event-stream`` response: each message becomes ``data:``; ``None`` becomes a ping (keeps the connection open)."""

    async def fluxo() -> AsyncIterator[str]:
        async for m in mensagens:
            yield f"data: {m}\n\n" if m is not None else ": ping\n\n"

    return StreamingResponse(fluxo(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})


canal = Canal()
