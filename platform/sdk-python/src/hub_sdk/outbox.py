"""Outbox transacional (``hub_outbox``) — mesmo esquema e semântica do Rust.

O app grava o evento **na mesma transação** da ação de negócio (:func:`write`); se a
transação não confirma, o evento não existe; se confirma, fica pendente até o hub buscá-lo
(``GET /{app}/_hub/outbox``) e confirmar (``POST /{app}/_hub/outbox/ack``). O hub deduplica
por ``event_id``: reentregas têm efeito uma vez só.

Sem banco, :meth:`Outbox.memory` guarda os eventos em memória (perdidos se o processo
cair — só para apps sem Postgres).
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any

from hub_sdk.schema import new_id

log = logging.getLogger("hub_sdk.outbox")

AUDIT = "audit"
# Onda 9: Inbox do hub (``meta`` com título, texto, link e destino).
NOTIFICATION = "notification"
APPROVAL_REQUEST = "approval_request"
# Máximo de eventos pendentes guardados no modo em memória.
MEM_CAP = 10_000
# Página máxima pedida ao outbox.
MAX_PENDING = 1000


@dataclass(frozen=True)
class Event:
    kind: str
    user: str
    action: str
    target: str = ""
    detail: str = ""
    # Tipos ``notification``/``approval_request``: título, texto, link, destino.
    meta: dict[str, Any] | None = None


@dataclass(frozen=True)
class Stored:
    """Evento como o hub o recebe (JSON do ``GET /_hub/outbox``)."""

    id: int
    event_id: str
    kind: str
    user: str
    action: str
    target: str
    detail: str
    at: int
    meta: dict[str, Any] | None = None

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        if d.get("meta") is None:
            d.pop("meta", None)
        return d


def _cut(s: str, n: int) -> str:
    return "".join(c for c in s if c.isprintable() or c == " ")[:n]


def audit(user: str, action: str, target: str = "", detail: str = "") -> Event:
    """Evento de auditoria (usuário ≤ 120, ação ≤ 80, alvo ≤ 200, detalhe ≤ 500; sem
    caracteres de controle) — mesmos cortes do Rust."""
    return Event(
        kind=AUDIT,
        user=_cut(user, 120),
        action=_cut(action, 80),
        target=_cut(target, 200),
        detail=_cut(detail, 500),
    )


def _list(v: list[str] | tuple[str, ...], n: int) -> list[str]:
    return [x for x in (_cut(s.strip(), 120) for s in v) if x][:n]


def _notice_meta(
    title: str,
    body: str,
    link: str,
    to_users: list[str] | tuple[str, ...],
    to_roles: list[str] | tuple[str, ...],
    level: str,
    approval_id: str | None = None,
) -> dict[str, Any]:
    m: dict[str, Any] = {
        "title": _cut(title, 120),
        "body": _cut(body, 500),
        "link": _cut(link, 300),
        "to_users": _list(to_users, 50),
        "to_roles": _list(to_roles, 20),
        "level": level if level in ("success", "warning", "error") else "info",
    }
    if approval_id is not None:
        m["approval_id"] = _cut(approval_id, 80)
    return m


def notification(
    user: str,
    title: str,
    body: str = "",
    link: str = "",
    to_users: list[str] | tuple[str, ...] = (),
    to_roles: list[str] | tuple[str, ...] = (),
    level: str = "info",
) -> Event:
    """Notificação para a Inbox do hub. ``link`` = caminho do próprio app (``/{id}/…``);
    ``to_users`` (logins) / ``to_roles`` (funções) vazios → só quem causou (``user``)."""
    return Event(
        kind=NOTIFICATION,
        user=_cut(user, 120),
        action=_cut(title, 80),
        meta=_notice_meta(title, body, link, to_users, to_roles, level),
    )


def approval_request(
    user: str,
    approval_id: str,
    title: str,
    body: str = "",
    link: str = "",
    to_users: list[str] | tuple[str, ...] = (),
    to_roles: list[str] | tuple[str, ...] = (),
    level: str = "warning",
) -> Event:
    """Pedido de aprovação: a decisão volta em ``POST /{app}/_hub/approvals/{approval_id}``
    (ver :mod:`hub_sdk.approvals`)."""
    return Event(
        kind=APPROVAL_REQUEST,
        user=_cut(user, 120),
        action=_cut(title, 80),
        target=_cut(approval_id, 80),
        meta=_notice_meta(title, body, link, to_users, to_roles, level, approval_id),
    )


def _payload(ev: Event) -> dict[str, Any]:
    p: dict[str, Any] = {"action": ev.action, "target": ev.target, "detail": ev.detail}
    if ev.meta is not None:
        p["meta"] = ev.meta
    return p


async def write(conn: Any, ev: Event) -> str:
    """Grava ``ev`` com ``conn`` — normalmente a transação da ação. Devolve o ``event_id``."""
    eid = new_id("evt")
    await conn.execute(
        "INSERT INTO hub_outbox (event_id, kind, user_name, payload) VALUES ($1, $2, $3, $4)",
        eid,
        ev.kind,
        ev.user,
        _payload(ev),
    )
    return eid


class Outbox:
    """Outbox no Postgres (``pool``) ou em memória (``pool=None``)."""

    def __init__(self, pool: Any | None) -> None:
        self.pool = pool
        self._lock = threading.Lock()
        self._next = 0
        self._mem: list[tuple[Stored, bool]] = []

    @classmethod
    def postgres(cls, pool: Any) -> Outbox:
        return cls(pool)

    @classmethod
    def memory(cls) -> Outbox:
        return cls(None)

    @property
    def durable(self) -> bool:
        return self.pool is not None

    async def emit(self, ev: Event) -> str | None:
        """Publica fora de uma transação (ações sem banco: exportação, consulta). Falha
        só vai para o log."""
        if self.pool is not None:
            try:
                async with self.pool.acquire() as c:
                    return await write(c, ev)
            except Exception as e:
                log.error("outbox: evento não gravado (%s): %s", e, ev.action)
                return None
        with self._lock:
            self._next += 1
            st = Stored(
                id=self._next,
                event_id=new_id("evt"),
                kind=ev.kind,
                user=ev.user,
                action=ev.action,
                target=ev.target,
                detail=ev.detail,
                at=int(time.time() * 1000),
                meta=ev.meta,
            )
            self._mem.append((st, False))
            if len(self._mem) > MEM_CAP:
                del self._mem[: len(self._mem) - MEM_CAP]
            return st.event_id

    async def pending(self, after: int, limit: int) -> list[Stored]:
        """Eventos não confirmados com ``id > after``, em ordem."""
        limit = max(1, min(limit, MAX_PENDING))
        if self.pool is not None:
            async with self.pool.acquire() as c:
                rows = await c.fetch(
                    "SELECT id, event_id, kind, user_name, payload, "
                    "(extract(epoch FROM created_at) * 1000)::int8 AS at "
                    "FROM hub_outbox WHERE acked_at IS NULL AND id > $1 ORDER BY id LIMIT $2",
                    after,
                    limit,
                )
            out = []
            for r in rows:
                p = r["payload"] if isinstance(r["payload"], dict) else {}
                out.append(
                    Stored(
                        id=r["id"],
                        event_id=r["event_id"],
                        kind=r["kind"],
                        user=r["user_name"],
                        action=str(p.get("action", "")),
                        target=str(p.get("target", "")),
                        detail=str(p.get("detail", "")),
                        at=r["at"],
                        meta=p.get("meta") if isinstance(p.get("meta"), dict) else None,
                    )
                )
            return out
        with self._lock:
            return [e for e, acked in self._mem if not acked and e.id > after][:limit]

    async def ack(self, event_ids: list[str]) -> int:
        """Confirma; devolve quantos passaram a confirmados (repetir é inócuo)."""
        if self.pool is not None:
            async with self.pool.acquire() as c:
                status = await c.execute(
                    "UPDATE hub_outbox SET acked_at = now() WHERE event_id = ANY($1::text[]) "
                    "AND acked_at IS NULL",
                    event_ids,
                )
            try:
                return int(str(status).rsplit(" ", 1)[-1])
            except ValueError:
                return 0
        wanted = set(event_ids)
        with self._lock:
            n = 0
            keep: list[tuple[Stored, bool]] = []
            for e, acked in self._mem:
                if not acked and e.event_id in wanted:
                    n += 1
                    continue
                keep.append((e, acked))
            self._mem = keep
            return n
