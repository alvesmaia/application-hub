"""Coletor real time: **per-user** notices for the open WebSockets
(``/coletor/ws/live``, through the hub gateway at ``/apps/coletor/ws/live``).

Whoever changes something (new reading, queued session) calls :func:`notify` **inside the
transaction**: ``pg_notify('coletor_live', …)`` is only delivered on commit, so the socket
never shows something that was not written — and it works with several processes/replicas (each
listens on the channel over its own connection). Without DB/listen (tests), delivery is local.

Each socket has a small queue (repeated notices merge: the socket redraws the
current state, not a diff).
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
            # Full queue: there is already a pending notice and the redraw covers this one.
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(topic)

    def is_listening(self) -> bool:
        return self.listening

    def count(self, user: str) -> int:
        return len(self.subs.get(user, ()))

    # ---- Postgres channel ----------------------------------------------------------------
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
    """Notifies the sockets of ``user`` (on commit of the transaction of ``c``)."""
    if c is not None and LIVE.listening:
        await c.execute("SELECT pg_notify($1, $2)", CHANNEL, json.dumps({"u": user, "t": topic}))
        return
    LIVE.deliver(user, topic)
