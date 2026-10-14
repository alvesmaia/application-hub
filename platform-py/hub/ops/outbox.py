"""Collects the apps' outbox (`GET {upstream}/{id}/_hub/outbox`, internal token, internal network): `audit` events go to the
audit log, `notification`/`approval_request` to the Inbox (and the SSE). Effect exactly once, in this order per page:
1. writes to the audit log (durable, with `source`); 2. records the ids "written, awaiting confirmation"; 3. confirms to the app
(`POST …/ack`, only the leader with a valid fence). A redelivery (a drop between 1 and 3) is deduplicated by the record and by `source`.
Only the leader collects."""

from __future__ import annotations

import asyncio
import re
from typing import Any

import httpx
from fastapi import FastAPI

from hub.admin.store import OutboxIngest
from hub.ops.leader import Leader
from hub.ux.inbox import approval_of, item_of, now_ms
from hub.ux.search import safe_app_link

TOKEN_HEADER, PAGE, MAX_BODY = "x-hub-internal-token", 200, 1 << 20
_ADMIN_PERMS = (1, 2, 3, 4)  # Users, Settings, Audit, Variables
_APPROVAL_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")


def valid_event_id(i: str) -> bool:
    return 0 < len(i) <= 80 and i.isascii() and i.isprintable() and " " not in i


def cut(s: Any, n: int) -> str:
    return "".join(c for c in str(s or "") if c.isprintable())[:n]


def str_list(v: Any, n: int) -> list[str]:
    return [
        t for x in (v if isinstance(v, list) else []) if isinstance(x, str) and (t := cut(x.strip(), 120))
    ][:n]


def allowed_roles(asked: list[str], declared: list[str], admin: list[str]) -> list[str]:
    """Roles an app can address: those in the manifest (`notify_roles`) or, with no declaration, any non-administrative one."""

    def norm(s: str) -> str:
        return s.strip().lower()

    pool = {norm(d) for d in declared}
    blocked = {norm(a) for a in admin}
    return [r for r in asked if (norm(r) in pool if pool else norm(r) not in blocked)]


def item_from_notice(app: str, ev: dict[str, Any], roles: list[str]) -> dict[str, Any] | None:
    """Inbox item; `None` when all requested recipients were refused (never falls back to "who caused it" by mistake)."""
    m: dict[str, Any] = ev["meta"] if isinstance(ev.get("meta"), dict) else {}
    to_users = str_list(m.get("to_users"), 50)
    if str_list(m.get("to_roles"), 20) and not roles and not to_users:
        return None
    level = m.get("level") if m.get("level") in ("success", "warning", "error") else "info"
    aid = str(m.get("approval_id") or "")
    approval = ev.get("kind") == "approval_request" and bool(_APPROVAL_ID.match(aid))
    return item_of(
        "approval" if approval else "notification",
        app,
        cut(m.get("title"), 120).strip() or "Notificação",
        body=cut(m.get("body"), 500),
        link=safe_app_link(app, str(m.get("link") or "")),
        level=level,
        at=int(ev["at"]) if int(ev.get("at") or 0) > 0 else now_ms(),
        source=f"{app}:{ev['event_id']}",
        by=cut(ev.get("user"), 120),
        to_users=to_users,
        to_roles=roles,
        approval=approval_of(aid) if approval else None,
    )


class Outbox:
    def __init__(self, app: FastAPI, client: httpx.AsyncClient, leader: Leader, ingest: OutboxIngest) -> None:
        self.app, self.client, self.leader, self.ingest = app, client, leader, ingest
        self.last_err: dict[str, str] = {}
        self.task: asyncio.Task[None] | None = None

    def _warn(self, app_id: str, msg: str) -> None:
        self.app.state.admin.logs.add("WARN", app_id, "plataforma", msg, "hub")

    def _roles(self, app_id: str, asked: list[str]) -> list[str]:
        e = self.app.state.gateway.registry.get(app_id)
        mf = e.manifest if e else None
        declared = list(
            getattr(mf, "notify_roles", None)
            or ((mf.model_extra or {}).get("notify_roles") if mf else None)
            or []
        )
        admin = [
            r["name"]
            for r in self.app.state.roles
            if any(len(r.get("permissions", [])) > i and r["permissions"][i] for i in _ADMIN_PERMS)
        ]
        return allowed_roles(asked, declared, admin)

    def _notice(self, app_id: str, ev: dict[str, Any]) -> None:
        meta: dict[str, Any] = ev["meta"] if isinstance(ev.get("meta"), dict) else {}
        item = item_from_notice(app_id, ev, self._roles(app_id, str_list(meta.get("to_roles"), 20)))
        if item is None:
            self._warn(app_id, "[inbox] item sem destinatário permitido; descartado")
        else:
            self.app.state.inbox.push(item)

    async def pull_app(self, app_id: str, upstream: str, token: str, after: int) -> dict[str, Any]:  # noqa: C901, PLR0915
        admin, base = self.app.state.admin, f"{upstream.rstrip('/')}/{app_id}/_hub/outbox"
        r = await self.client.get(
            f"{base}?after={after}&limit={PAGE}", headers={TOKEN_HEADER: token}, timeout=10
        )
        if r.status_code >= 300:
            raise OSError(f"outbox respondeu {r.status_code}")
        if len(r.content) > MAX_BODY:
            raise OSError(f"resposta maior que {MAX_BODY // 1024} KiB")
        events = list(r.json()["events"])[:PAGE]
        out = {
            "fetched": len(events),
            "recorded": 0,
            "last_seq": max((int(e["id"]) for e in events), default=after),
        }
        pending, seen = set(await self.ingest.pending(app_id)), []
        async with admin.locked() as data:
            in_audit = {
                e["source"] for e in data.get("audit", []) if e.get("scope") == app_id and e.get("source")
            }
        for ev in events:
            eid = str(ev.get("event_id", ""))
            if not valid_event_id(eid):
                self._warn(
                    app_id,
                    f"[auditoria] evento #{ev.get('id')} do outbox com event_id inválido: não gravado nem confirmado",
                )
                continue
            if any(s == eid for s, _ in seen):
                continue
            seen.append((eid, int(ev["id"])))
            if eid in pending or eid in in_audit:
                continue
            if ev.get("kind") in ("notification", "approval_request"):
                self._notice(app_id, ev)
            elif ev.get("kind") == "audit":
                tgt, det = cut(ev.get("target"), 200), cut(ev.get("detail"), 500)
                user = cut(ev.get("user"), 120).strip() or "desconhecido"
                detail = f"{tgt} · {det}" if tgt and det else tgt or det
                await admin.audit(
                    user, cut(ev.get("action"), 80), detail, icon="clock", scope=app_id, source=eid
                )
                out["recorded"] += 1
        await self.ingest.recorded(app_id, seen, out["recorded"])
        ids = await self.ingest.pending(app_id)
        if ids and await self.leader.checked():
            for i in range(0, len(ids), 1000):
                chunk = ids[i : i + 1000]
                a = await self.client.post(
                    f"{base}/ack", headers={TOKEN_HEADER: token}, json={"ids": chunk}, timeout=10
                )
                if a.status_code >= 300:
                    break
                await self.ingest.acked(app_id, chunk)
        return out

    async def tick(self) -> None:
        """One round: each app with an internal token, several pages if there is a backlog."""
        for e in self.app.state.gateway.registry.entries():
            if not (token := self.app.state.admin.tokens.get(e.id)):
                continue
            after = 0
            for _ in range(20):
                try:
                    p = await self.pull_app(e.id, e.upstream, token, after)
                    self.last_err.pop(e.id, None)
                    if p["fetched"] < PAGE or p["last_seq"] <= after:
                        break
                    after = p["last_seq"]
                except (httpx.HTTPError, OSError, ValueError, KeyError) as ex:
                    msg = f"sem resposta ({ex})" if isinstance(ex, httpx.HTTPError) else str(ex)
                    if self.last_err.get(e.id) != msg:  # one warning per state change
                        self._warn(e.id, f"[auditoria] outbox indisponível: {msg}")
                        self.last_err[e.id] = msg
                    break

    def start(self, every: float) -> None:
        async def loop() -> None:
            while True:
                await asyncio.sleep(every)
                if await self.leader.checked():
                    self.leader.ran("outbox")
                    await self.tick()

        self.task = asyncio.create_task(loop())

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
