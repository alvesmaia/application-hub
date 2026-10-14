"""Tempo real (SSE ou WebSocket): acorda as conexões abertas quando os dados mudam.

Com Postgres (``DATABASE_URL`` no hub), o aviso passa por ``LISTEN``/``NOTIFY`` e alcança todas as réplicas;
sem ele (SQLite no ``hub dev``), fica no próprio processo. Guia: ``docs/dev/guias/tempo-real.md``.

    from hub_sdk.realtime import canal, sse

    # lifespan:  await canal.iniciar() ... await canal.fechar()
    # SSE:       return sse(canal.mensagens(lambda: json.dumps({"html": ...})))
    # WebSocket: async for m in canal.mensagens(...): if m is not None: await ws.send_text(m)
    # mudança:   depois do commit, canal.avisar(engine)
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
        """No lifespan do app. Com Postgres, escuta o aviso de qualquer réplica."""
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
            with contextlib.suppress(asyncio.QueueFull):  # já há um aviso pendente
                fila.put_nowait(None)

    def avisar(self, engine: Any = None) -> None:
        """Chame depois do commit (vale em rotas ``def`` e ``async def``). ``engine``: o SQLAlchemy do app;
        no Postgres o aviso vai por ``pg_notify`` (todas as réplicas)."""
        if engine is not None and engine.dialect.name == "postgresql":
            import sqlalchemy as sa

            with engine.begin() as c:
                c.execute(sa.text("SELECT pg_notify(:canal, '')"), {"canal": self.nome})
        elif self.loop is not None:
            self.loop.call_soon_threadsafe(self._acordar)

    async def mensagens(
        self, mensagem: Callable[[], str], ping: float = 20.0
    ) -> AsyncGenerator[str | None, None]:
        """O estado atual na conexão e depois a cada aviso; ``None`` = nada novo em ``ping`` segundos."""
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
    """Resposta ``text/event-stream``: cada mensagem vira ``data:``; ``None`` vira ping (mantém a conexão)."""

    async def fluxo() -> AsyncIterator[str]:
        async for m in mensagens:
            yield f"data: {m}\n\n" if m is not None else ": ping\n\n"

    return StreamingResponse(fluxo(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})


canal = Canal()
