"""Inbox: notificações e pedidos de aprovação dos apps, pedidos de acesso e ações de agente.

Estado em `inbox.json` (escrita atômica). Cada pessoa vê só o que a alcança (`targets`); a leitura e o
arquivamento são por usuário. Mudanças acordam quem escuta o SSE (`/api/me/stream`)."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from functools import wraps
from pathlib import Path
from typing import Any

from hub.filelock import file_lock
from hub.ux.texts import ago

MAX_ITEMS = 5000
ACCESS_PENDING_MAX = 5
ACCESS_COOLDOWN_MS = 24 * 3600 * 1000
STEPUP_TTL_MS = 120_000
OPEN = ("pending", "sending", "error")


class InboxError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status, self.message = status, message


@dataclass
class Who:
    """Quem olha a Inbox (vindo da sessão)."""

    id: str
    email: str = ""
    name: str = ""
    roles: list[str] = field(default_factory=list)
    perms: list[str] = field(default_factory=list)
    apps: list[str] | None = None  # `None` = todos
    session: bool = True  # False = token de API

    def can_app(self, app: str) -> bool:
        return self.apps is None or app in self.apps


def _norm(s: str) -> str:
    return s.strip().lower()


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def targets(item: dict[str, Any], who: Who) -> bool:
    """O item é para `who`? Casa por e-mail/id (nunca pelo nome de exibição)."""
    me = {x for x in (_norm(who.email), _norm(who.id)) if x}
    user_hit = any(_norm(u) in me for u in item["to_users"] if u.strip())
    role_hit = any(_norm(r) in {_norm(x) for x in who.roles} for r in item["to_roles"])
    perm_hit = bool(item["to_perm"]) and item["to_perm"] in who.perms
    nobody = not (item["to_users"] or item["to_roles"] or item["to_perm"])
    mine = nobody and ((bool(item["by"].strip()) and _norm(item["by"]) in me) or item["by_id"] == who.id)
    hit = user_hit or role_hit or perm_hit or mine
    match item["kind"]:
        case "access":
            return bool(hit or (item["by_id"] and item["by_id"] == who.id))
        case "agent":
            return bool(user_hit)
        case _:
            return bool(hit and who.can_app(item["app"]))


def can_decide(item: dict[str, Any], who: Who) -> bool:
    not_self = item["by_id"] != who.id and (not item["by"].strip() or _norm(item["by"]) != _norm(who.email))
    match item["kind"]:
        case "approval":
            return bool(not_self and "aprovar_solicitacoes" in who.perms and targets(item, who))
        case "access":
            return bool(not_self and "gerenciar_usuarios" in who.perms and who.can_app(item["app"]))
        case "agent":
            return bool(who.session and targets(item, who))
    return False


def item_of(kind: str, app: str, title: str, **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": 0,
        "kind": kind,
        "app": app,
        "title": title,
        "body": "",
        "link": "",
        "level": "info",
        "at": now_ms(),
        "source": "",
        "by": "",
        "by_id": "",
        "to_users": [],
        "to_roles": [],
        "to_perm": "",
        "approval": None,
        "agent": None,
    }
    return base | kw


def approval_of(ext_id: str) -> dict[str, Any]:
    return {
        "ext_id": ext_id,
        "status": "pending",
        "decided_by": "",
        "decided_at": 0,
        "comment": "",
        "error": "",
    }


def _txn[T](fn: Callable[..., T]) -> Callable[..., T]:
    """Mutação sob a trava de `inbox.json` (réplicas no mesmo volume): relê o que outra réplica gravou antes."""

    @wraps(fn)
    def run(self: InboxStore, *a: Any, **k: Any) -> T:
        with file_lock(self.path):
            self.reload_if_changed()
            return fn(self, *a, **k)

    return run


class InboxStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.items: dict[int, dict[str, Any]] = {}
        self.states: dict[str, dict[str, int]] = {}  # usuário → {id: bit0 lido | bit1 arquivado}
        self.grants: dict[str, list[str]] = {}  # acessos concedidos por pedido aprovado
        self.next_id = 1
        self.watchers: set[asyncio.Queue[None]] = set()
        self.streams: dict[str, int] = {}
        self._stepup: dict[str, dict[str, Any]] = {}
        self._mtime = 0
        self._load()

    def _load(self) -> None:
        with suppress(OSError, ValueError, KeyError):
            self._mtime = self.path.stat().st_mtime_ns
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.items = {int(i["id"]): i for i in raw["items"]}
            self.states, self.grants, self.next_id = raw["states"], raw["grants"], raw["next_id"]

    def reload_if_changed(self) -> bool:
        """Outra réplica gravou `inbox.json` (volume compartilhado): relê e acorda o SSE desta réplica."""
        try:
            changed = self.path.stat().st_mtime_ns != self._mtime
        except OSError:
            return False
        if changed:
            self._load()
            self.poke()
        return changed

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(f".json.{os.getpid()}.tmp")
        data = {
            "items": list(self.items.values()),
            "states": self.states,
            "grants": self.grants,
            "next_id": self.next_id,
        }
        tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, self.path)
        with suppress(OSError):
            self._mtime = self.path.stat().st_mtime_ns
        self.poke()

    def poke(self) -> None:
        """Acorda quem escuta o SSE (algo mudou para alguém)."""
        for q in self.watchers:
            if q.empty():
                q.put_nowait(None)

    def grants_for(self, user_id: str) -> list[str]:
        return self.grants.get(user_id, [])

    def item(self, item_id: int) -> dict[str, Any] | None:
        return self.items.get(item_id)

    @_txn
    def push(self, item: dict[str, Any]) -> int:
        """Item novo; a mesma `source` não duplica (devolve o existente)."""
        if item["source"]:
            for i in self.items.values():
                if i["source"] == item["source"]:
                    return int(i["id"])
        item = item | {"id": self.next_id}
        if item.get("kind") == "agent":  # pedido de ação de agente: a tela de aprovação (com reautenticação)
            item["link"] = f"/account/agent-approval/{item['id']}"
        self.next_id += 1
        self.items[item["id"]] = item
        if len(self.items) > MAX_ITEMS:  # saem os informativos mais antigos; pedidos abertos ficam
            old = sorted(self.items.values(), key=lambda x: x["at"])
            gone = [i["id"] for i in old if not (i["approval"] and i["approval"]["status"] in OPEN)]
            for g in gone[: len(self.items) - MAX_ITEMS]:
                del self.items[g]
        self._save()
        return int(item["id"])

    def entries(
        self, who: Who, lang: str, channel_on: Callable[[str], bool], names: Callable[[str], str]
    ) -> list[dict[str, Any]]:
        """Itens de `who`, mais novos primeiro; `channel_on(tipo)` = canal "no hub" ligado."""
        now = now_ms()
        out = []
        for it in self.items.values():
            if not targets(it, who):
                continue
            st = self.states.get(who.id, {}).get(str(it["id"]), 0)
            ap = it["approval"]
            if ap and it["agent"] and ap["status"] == "pending" and it["agent"]["expires_at"] <= now:
                ap = ap | {"status": "expired"}
            decide = bool(ap) and ap["status"] in ("pending", "error") and can_decide(it, who)
            out.append(
                {
                    "id": f"i:{it['id']}",
                    "kind": it["kind"],
                    "app": it["app"],
                    "app_name": names(it["app"]),
                    "title": it["title"],
                    "body": it["body"],
                    "link": it["link"],
                    "level": it["level"],
                    "at": it["at"],
                    "when": ago(it["at"], now, lang),
                    "read": bool(st & 1),
                    "archived": bool(st & 2),
                    "in_app": channel_on("notification" if it["kind"] == "notification" else "approval")
                    or decide,
                    "approval": ap
                    and {
                        "status": ap["status"],
                        "can_decide": decide,
                        "by": it["by"],
                        "decided_by": ap["decided_by"],
                        "comment": ap["comment"],
                        "error": ap["error"],
                        "can_revoke": it["kind"] == "access"
                        and ap["status"] == "approved"
                        and "gerenciar_usuarios" in who.perms,
                    },
                }
            )
        return sorted(out, key=lambda e: -int(e["at"]))

    @staticmethod
    def unread(entries: list[dict[str, Any]]) -> int:
        return sum(1 for e in entries if not e["read"] and not e["archived"] and e["in_app"])

    @_txn
    def set_state(self, who: Who, ids: list[int], read: bool | None, archived: bool | None) -> int:
        mine = self.states.setdefault(who.id, {})
        n = 0
        for i in ids:
            it = self.items.get(i)
            if not it or not targets(it, who):
                continue
            bits = mine.get(str(i), 0)
            for flag, on in ((1, read), (2, archived)):
                if on is not None:
                    bits = bits | flag if on else bits & ~flag
            mine[str(i)] = bits | (1 if archived else 0)
            n += 1
        self._save()
        return n

    def request_access(self, who: Who, app: str, app_name: str, reason: str) -> int:
        now = now_ms()
        mine = [i for i in self.items.values() if i["kind"] == "access" and i["by_id"] == who.id]
        pend = [i for i in mine if i["approval"]["status"] in OPEN]
        if any(i["app"] == app for i in pend):
            raise InboxError(409, "Já existe um pedido pendente para esta aplicação.")
        if len(pend) >= ACCESS_PENDING_MAX:
            raise InboxError(429, "Muitos pedidos de acesso pendentes; aguarde a análise.")
        last = max(
            (
                i["approval"]["decided_at"]
                for i in mine
                if i["app"] == app and i["approval"]["status"] == "rejected"
            ),
            default=0,
        )
        if last and now - last < ACCESS_COOLDOWN_MS:
            raise InboxError(429, "O último pedido para esta aplicação foi recusado; tente de novo em 24 h.")
        return self.push(
            item_of(
                "access",
                app,
                f"{who.name} pede acesso a {app_name}",
                body="".join(c for c in reason if c.isprintable())[:300],
                link="/admin/users?tab=roles",
                at=now,
                source=f"access:{who.id}:{app}:{now}",
                by=who.email or who.name,
                by_id=who.id,
                to_perm="gerenciar_usuarios",
                approval=approval_of(f"req-{now}"),
            )
        )

    @_txn
    def revoke_grant(self, item_id: int) -> bool:
        it = self.items.get(item_id)
        if not it or it["kind"] != "access" or it["approval"]["status"] != "approved":
            return False
        self.grants[it["by_id"]] = [a for a in self.grants.get(it["by_id"], []) if a != it["app"]]
        it["approval"]["status"] = "revoked"
        self._save()
        return True

    @_txn
    def begin(
        self, who: Who, item_id: int, decision: str, comment: str, app_name: str = ""
    ) -> dict[str, Any]:
        """Valida e decide: acesso e agente na hora; pedido de app reserva (`sending`) até o app confirmar."""
        it = self.items.get(item_id)
        if not it or not it["approval"] or not targets(it, who):
            raise InboxError(404, "Pedido não encontrado.")
        if decision not in ("approve", "reject"):
            raise InboxError(400, "Decisão inválida.")
        if not can_decide(it, who):
            raise InboxError(403, "Você não pode decidir este pedido.")
        ap = it["approval"]
        if it["agent"] and ap["status"] == "pending" and it["agent"]["expires_at"] <= now_ms():
            ap["status"] = "expired"
            self._save()
        if ap["status"] not in ("pending", "error"):
            raise InboxError(409, "Este pedido já foi decidido ou expirou.")
        final = "approved" if decision == "approve" else "rejected"
        ap.update(decided_by=who.email or who.name, decided_at=now_ms(), comment=comment, error="")
        ap["status"] = "sending" if it["kind"] == "approval" else final
        if it["kind"] == "access" and final == "approved":
            apps = self.grants.setdefault(it["by_id"], [])
            if it["app"] not in apps:
                apps.append(it["app"])
        if it["kind"] == "access":  # a pessoa que pediu recebe o resultado na Inbox (como no hub Rust)
            name = app_name or it["app"]
            self.push(
                item_of(
                    "notification",
                    it["app"],
                    f"Acesso a {name} liberado"
                    if final == "approved"
                    else f"Pedido de acesso a {name} recusado",
                    body=comment,
                    link=f"/apps/{it['app']}",
                    level="success" if final == "approved" else "warning",
                    source=f"access-result:{item_id}",
                    by=who.email or who.name,
                    to_users=[it["by_id"]],
                )
            )
        self._save()
        return it

    @_txn
    def finish(self, item_id: int, status: str | None, error: str = "") -> None:
        """Fim da chamada ao app: `status` final ou `None` (falhou: volta a `error`, dá para tentar de novo)."""
        ap = self.items[item_id]["approval"]
        ap["status"], ap["error"] = status or "error", error
        self._save()

    # step-up: aprovar ação de agente exige confirmar a senha de novo (token de uso único)
    def stepup_issue(self, user: str, item_id: int, args_hash: str) -> str:
        tok, now = f"stp_{secrets.token_hex(16)}", now_ms()
        self._stepup = {k: v for k, v in self._stepup.items() if v["expires"] > now}
        self._stepup[tok] = {"user": user, "item": item_id, "hash": args_hash, "expires": now + STEPUP_TTL_MS}
        return tok

    def stepup_consume(self, tok: str, user: str, item_id: int, args_hash: str) -> bool:
        g = self._stepup.pop(tok, None)
        return bool(
            g
            and g["user"] == user
            and g["item"] == item_id
            and g["hash"] == args_hash
            and g["expires"] > now_ms()
        )
