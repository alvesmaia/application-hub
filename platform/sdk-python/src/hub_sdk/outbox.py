"""Outbox transacional (``hub_outbox``).

The app writes the event **in the same transaction** as the business action (:func:`write`); if the
transaction does not commit, the event does not exist; if it commits, it stays pending until the hub fetches it
(``GET /{app}/_hub/outbox``) e confirmar (``POST /{app}/_hub/outbox/ack``). O hub deduplica
by ``event_id``: redeliveries have a single effect.

Without a DB, :meth:`Outbox.memory` keeps the events in memory (lost if the process
dies — only for apps without Postgres).
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
# Wave 9: hub Inbox (``meta`` with title, text, link and target).
NOTIFICATION = "notification"
APPROVAL_REQUEST = "approval_request"
# Maximum pending events kept in in-memory mode.
MEM_CAP = 10_000
# Maximum page requested from the outbox.
MAX_PENDING = 1000


@dataclass(frozen=True)
class Event:
    kind: str
    user: str
    action: str
    target: str = ""
    detail: str = ""
    # ``notification``/``approval_request`` types: title, text, link, target.
    meta: dict[str, Any] | None = None


@dataclass(frozen=True)
class Stored:
    """Event as the hub receives it (JSON of ``GET /_hub/outbox``)."""

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
    """Audit event (user ≤ 120, action ≤ 80, target ≤ 200, detail ≤ 500; without
    control characters)."""
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
    """Notification for the hub Inbox. ``link`` = path of the app itself (``/{id}/…``);
    ``to_users`` (logins) / ``to_roles`` (roles) empty → only whoever caused it (``user``)."""
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
    """Approval request: the decision comes back at ``POST /{app}/_hub/approvals/{approval_id}``
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
    """Writes ``ev`` with ``conn`` — usually the action's transaction. Returns the ``event_id``."""
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
    """Outbox in Postgres (``pool``) or in memory (``pool=None``)."""

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
        """Publishes outside a transaction (actions without a DB: export, query). Failure
        only goes to the log."""
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
        """Unconfirmed events with ``id > after``, in order."""
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
        """Confirms; returns how many became confirmed (repeating is harmless)."""
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
