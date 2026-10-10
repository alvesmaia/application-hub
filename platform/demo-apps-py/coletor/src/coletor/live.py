"""Tempo real do Coletor: avisos **por usuário** para os WebSockets abertos
(``/coletor/ws/live``, pelo gateway do hub em ``/apps/coletor/ws/live``).

Quem muda algo (leitura nova, sessão da fila) chama :func:`notify` **dentro da
transação**: ``pg_notify('coletor_live', …)`` só é entregue no commit, então o socket
nunca mostra algo que não foi gravado — e funciona com vários processos/réplicas (cada
um escuta o canal numa conexão própria). Sem banco/escuta (testes), a entrega é local.

Cada socket tem uma fila pequena (avisos repetidos se fundem: o socket redesenha o
estado atual, não um diff).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

import asyncpg

log = logging.getLogger("coletor.live")
CHANNEL = "coletor_live"


class Live:
    def __init__(self) -> None:
        self.subs: dict[str, set[asyncio.Queue[str]]] = {}
        self.conn: Any = None
        self.listening = False
        self._lock = asyncio.Lock()

    # ---- assinantes ---------------------------------------------------------------------
    def subscribe(self, user: str) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=8)
        self.subs.setdefault(user, set()).add(q)
        return q

    def unsubscribe(self, user: str, q: asyncio.Queue[str]) -> None:
        s = self.subs.get(user)
        if s is not None:
            s.discard(q)
            if not s:
                self.subs.pop(user, None)

    def deliver(self, user: str, topic: str) -> None:
        for q in list(self.subs.get(user, ())):
            # Fila cheia: já há um aviso pendente e o redesenho cobre este.
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(topic)

    def is_listening(self) -> bool:
        return self.listening

    def count(self, user: str) -> int:
        return len(self.subs.get(user, ()))

    # ---- canal do Postgres ----------------------------------------------------------------
    def _on_notify(self, _c: Any, _pid: int, _ch: str, payload: str) -> None:
        try:
            d = json.loads(payload)
            self.deliver(str(d["u"]), str(d.get("t", "")))
        except (ValueError, KeyError, TypeError):
            return

    def _on_lost(self, _c: Any) -> None:
        self.listening = False
        self.conn = None

    async def ensure_listener(self, dsn: str | None) -> None:
        if self.listening or dsn in (None, ""):
            return
        async with self._lock:
            if self.is_listening():  # outro socket ligou durante a espera
                return
            try:
                conn = await asyncpg.connect(dsn, timeout=5)
                await conn.add_listener(CHANNEL, self._on_notify)
                conn.add_termination_listener(self._on_lost)
            except (TimeoutError, OSError, asyncpg.PostgresError) as e:
                log.warning("coletor: escuta do tempo real indisponível (%s); entrega local", e)
                return
            self.conn = conn
            self.listening = True

    async def close(self) -> None:
        c, self.conn, self.listening = self.conn, None, False
        if c is not None:
            with contextlib.suppress(OSError, asyncpg.PostgresError):
                await c.close()


LIVE = Live()


async def notify(c: Any, user: str, topic: str) -> None:
    """Avisa os sockets de ``user`` (no commit da transação de ``c``)."""
    if c is not None and LIVE.listening:
        await c.execute("SELECT pg_notify($1, $2)", CHANNEL, json.dumps({"u": user, "t": topic}))
        return
    LIVE.deliver(user, topic)
