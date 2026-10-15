"""Inbox: app notifications and approval requests, plus access requests.

State in `inbox.json` (atomic write). Each person sees only what reaches them (`targets`); reading and
archiving are per user. Changes wake whoever listens on the SSE (`/api/me/stream`)."""

from __future__ import annotations

import asyncio
import json
import os
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
OPEN = ("pending", "sending", "error")


class InboxError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status, self.message = status, message


@dataclass
class Who:
    """Who is looking at the Inbox (from the session)."""

    id: str
    email: str = ""
    name: str = ""
    roles: list[str] = field(default_factory=list)
    perms: list[str] = field(default_factory=list)
    apps: list[str] | None = None  # `None` = all
    session: bool = True  # False = API token

    def can_app(self, app: str) -> bool:
        return self.apps is None or app in self.apps


def _norm(s: str) -> str:
    return s.strip().lower()


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def targets(item: dict[str, Any], who: Who) -> bool:
    """Is the item for `who`? Matches by email/id (never by display name)."""
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
        case _:
            return bool(hit and who.can_app(item["app"]))


def can_decide(item: dict[str, Any], who: Who) -> bool:
    not_self = item["by_id"] != who.id and (not item["by"].strip() or _norm(item["by"]) != _norm(who.email))
    match item["kind"]:
        case "approval":
            return bool(not_self and "aprovar_solicitacoes" in who.perms and targets(item, who))
        case "access":
            return bool(not_self and "gerenciar_usuarios" in who.perms and who.can_app(item["app"]))
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
    """Mutation under the `inbox.json` lock (replicas on the same volume): rereads what another replica wrote first."""

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
        self.states: dict[str, dict[str, int]] = {}  # user → {id: bit0 read | bit1 archived}
        self.grants: dict[str, list[str]] = {}  # access granted by an approved request
        self.next_id = 1
        self.watchers: set[asyncio.Queue[None]] = set()
        self.streams: dict[str, int] = {}
        self._mtime = 0
        self._load()

    def _load(self) -> None:
        with suppress(OSError, ValueError, KeyError):
            self._mtime = self.path.stat().st_mtime_ns
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            # agent action requests (approval in the Inbox, now removed) are no longer shown
            self.items = {int(i["id"]): i for i in raw["items"] if i.get("kind") != "agent"}
            self.states, self.grants, self.next_id = raw["states"], raw["grants"], raw["next_id"]

    def reload_if_changed(self) -> bool:
        """Another replica wrote `inbox.json` (shared volume): rereads it and wakes this replica's SSE."""
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
        """Wakes whoever listens on the SSE (something changed for someone)."""
        for q in self.watchers:
            if q.empty():
                q.put_nowait(None)

    def grants_for(self, user_id: str) -> list[str]:
        return self.grants.get(user_id, [])

    def item(self, item_id: int) -> dict[str, Any] | None:
        return self.items.get(item_id)

    @_txn
    def push(self, item: dict[str, Any]) -> int:
        """New item; the same `source` is not duplicated (returns the existing one)."""
        if item["source"]:
            for i in self.items.values():
                if i["source"] == item["source"]:
                    return int(i["id"])
        item = item | {"id": self.next_id}
        self.next_id += 1
        self.items[item["id"]] = item
        if len(self.items) > MAX_ITEMS:  # the oldest notices go; open requests stay
            old = sorted(self.items.values(), key=lambda x: x["at"])
            gone = [i["id"] for i in old if not (i["approval"] and i["approval"]["status"] in OPEN)]
            for g in gone[: len(self.items) - MAX_ITEMS]:
                del self.items[g]
        self._save()
        return int(item["id"])

    def entries(
        self, who: Who, lang: str, channel_on: Callable[[str], bool], names: Callable[[str], str]
    ) -> list[dict[str, Any]]:
        """Items for `who`, newest first; `channel_on(kind)` = the "in the hub" channel is on."""
        now = now_ms()
        out = []
        for it in self.items.values():
            if not targets(it, who):
                continue
            st = self.states.get(who.id, {}).get(str(it["id"]), 0)
            ap = it["approval"]
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

    def _notify_access_result(
        self, who: Who, it: dict[str, Any], item_id: int, final: str, comment: str, app_name: str
    ) -> None:
        """The person who asked for access receives the result in the Inbox."""
        name = app_name or it["app"]
        self.push(
            item_of(
                "notification",
                it["app"],
                f"Acesso a {name} liberado" if final == "approved" else f"Pedido de acesso a {name} recusado",
                body=comment,
                link=f"/apps/{it['app']}",
                level="success" if final == "approved" else "warning",
                source=f"access-result:{item_id}",
                by=who.email or who.name,
                to_users=[it["by_id"]],
            )
        )

    def _decidable(self, who: Who, item_id: int, decision: str) -> dict[str, Any]:
        """The item `who` may decide now, or the InboxError that says why not (checked in this order)."""
        it = self.items.get(item_id)
        if not it or not it["approval"] or not targets(it, who):
            raise InboxError(404, "Pedido não encontrado.")
        if decision not in ("approve", "reject"):
            raise InboxError(400, "Decisão inválida.")
        if not can_decide(it, who):
            raise InboxError(403, "Você não pode decidir este pedido.")
        if it["approval"]["status"] not in ("pending", "error"):
            raise InboxError(409, "Este pedido já foi decidido ou expirou.")
        return it

    @_txn
    def begin(
        self, who: Who, item_id: int, decision: str, comment: str, app_name: str = ""
    ) -> dict[str, Any]:
        """Validates and decides: access immediately; an app request holds a reservation (`sending`) until the app confirms."""
        it = self._decidable(who, item_id, decision)
        ap = it["approval"]
        final = "approved" if decision == "approve" else "rejected"
        ap.update(decided_by=who.email or who.name, decided_at=now_ms(), comment=comment, error="")
        ap["status"] = "sending" if it["kind"] == "approval" else final
        if it["kind"] == "access" and final == "approved":
            apps = self.grants.setdefault(it["by_id"], [])
            if it["app"] not in apps:
                apps.append(it["app"])
        if it["kind"] == "access":
            self._notify_access_result(who, it, item_id, final, comment, app_name)
        self._save()
        return it

    @_txn
    def finish(self, item_id: int, status: str | None, error: str = "") -> None:
        """End of the call to the app: final `status` or `None` (failed: goes back to `error`, can be retried)."""
        ap = self.items[item_id]["approval"]
        ap["status"], ap["error"] = status or "error", error
        self._save()
