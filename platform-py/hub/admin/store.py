"""Shared admin.json persistence, log bus, and outbox acknowledgements."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections import deque
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager, nullcontext, suppress
from pathlib import Path
from typing import Any, cast

from hub.admin.chain import AuditChain
from hub.admin.config import INTERNAL_TOKEN_ENV, injected_name, seed
from hub.admin.config_env import ConfigOps
from hub.models import AdminDocument, AuditEntry
from hub.state import StateDb
from hub.vault import Vault, is_sealed

_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")


class AdminStore:
    def __init__(
        self,
        data_dir: Path,
        vault: Vault | None = None,
        db: StateDb | None = None,
        apps: list[str] | None = None,
    ) -> None:
        self.path, self.vault, self.db = data_dir / "admin.json", vault, db
        self.apps: list[str] = apps or []
        self.tokens: dict[str, str] = {}
        self.cfg = ConfigOps(self)
        self.logs = LogBus()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = asyncio.Lock()
        self.chain = AuditChain(data_dir / "audit", vault, db)
        if not db:
            self._reconcile()

    async def reconcile(self) -> None:
        if self.db:
            async with self.db.file_lock("admin.json"):
                self._reconcile()

    def _reconcile(self) -> None:
        if self.chain.blocked:
            return
        existing = self._read().get("audit", [])
        report = self.chain.verify()
        event_id = 0
        for line in self.chain._lines():
            if line.get("t") == "e":
                event_id = json.loads(line["event"])["id"]
        for event in sorted((e for e in existing if e.get("id", 0) > event_id), key=lambda e: e["id"]):
            self.chain._append(event, migrated=report.last_seq == 0)
            report = self.chain.verify()

    @asynccontextmanager
    async def locked(self, write: bool = False) -> AsyncIterator[dict[str, Any]]:
        # The write stays inside the cross-replica lock, or another replica can overwrite it.
        async with self.lock, self.db.file_lock("admin.json", write=write) if self.db else nullcontext():
            data = self._read()
            yield data
            if write:
                self._write(data)

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            data: dict[str, Any] = {
                "settings": {},
                "env": [],
                "roles": [],
                "health": [],
                "audit": [],
                "next_audit_id": 0,
            }
        else:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        AdminDocument.model_validate(data)
        seed(data, self.apps, with_env=self.vault is not None)
        for item in data.get("env", []):
            value = item.get("value", "")
            if is_sealed(value):
                if not self.vault:
                    raise ValueError("admin.json has encrypted values but vault is unavailable")
                item["value"] = self.vault.open_value(value, f"admin.env:{item['name']}")
        self._refresh_tokens(data)
        return cast(dict[str, Any], data)

    def _refresh_tokens(self, data: dict[str, Any]) -> None:
        """Internal tokens (managed variables) in memory: search and the Inbox read them without touching disk."""
        self.tokens = {
            b["app"]: v["value"]
            for v in data.get("env", [])
            if v.get("managed") and injected_name(v) == INTERNAL_TOKEN_ENV
            for b in v.get("bindings", [])
        }

    def _write(self, data: dict[str, Any]) -> None:
        AdminDocument.model_validate(data)
        self._refresh_tokens(data)
        disk = json.loads(json.dumps(data))
        for item in disk.get("env", []):
            if item.get("kind") != "publica":
                if not self.vault:
                    raise ValueError("vault required to write secret environment values")
                item["value"] = self.vault.seal(item["value"], f"admin.env:{item['name']}")
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(disk, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        with suppress(OSError):
            tmp.chmod(0o600)
        os.replace(tmp, self.path)

    def _push_audit(
        self,
        data: dict[str, Any],
        actor: str,
        title: str,
        detail: str,
        icon: str,
        scope: str,
        source: str = "",
    ) -> dict[str, Any]:
        data.setdefault("audit", [])
        data["next_audit_id"] = data.get("next_audit_id", 0) + 1
        event = {
            "id": data["next_audit_id"],
            "at": time.time_ns() // 1_000_000,
            "actor": actor,
            "icon": icon,
            "title": title,
            "detail": detail,
            "scope": scope,
        }
        if source:  # event id in the app's outbox (deduplicates redeliveries)
            event["source"] = source
        AuditEntry.model_validate(event)
        data["audit"].append(event)
        data["audit"] = data["audit"][-5000:]
        self.logs.add("INFO", scope, actor, f"[admin] {title} · {detail}", "admin", event["at"])
        return event

    async def audit(
        self,
        actor: str,
        title: str,
        detail: str,
        *,
        icon: str = "check",
        scope: str = "platform",
        source: str = "",
    ) -> dict[str, Any]:
        async with self.locked(True) as data:
            event = self._push_audit(data, actor, title, detail, icon, scope, source)
        await self.chain.append_shared(event)
        return event

    async def env_put(self, name: str, value: str, kind: str = "secreta") -> None:
        if not _NAME.fullmatch(name) or kind not in ("publica", "secreta", "connection_string"):
            raise ValueError("invalid environment variable")
        async with self.locked(True) as data:
            items = data.setdefault("env", [])
            item = next((v for v in items if v.get("name") == name), None)
            if item is None:
                items.append({"name": name, "value": value, "kind": kind, "bindings": []})
            else:
                item.update(value=value, kind=kind)


_GMT3 = -3 * 3600


def log_stamp(ms: int) -> str:
    t = time.gmtime(ms / 1000 + _GMT3)
    return f"{t.tm_mday:02}/{t.tm_mon:02} {t.tm_hour:02}:{t.tm_min:02}:{t.tm_sec:02}"


class LogBus:
    """Log bus: in-memory circular buffer + broadcast to SSE (real events only)."""

    def __init__(self) -> None:
        self.lines: deque[dict[str, Any]] = deque(maxlen=1000)
        self.subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    def push(self, line: dict[str, Any]) -> None:
        self.lines.append(line)
        for queue in tuple(self.subscribers):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(line)

    def add(self, level: str, app: str, user: str, message: str, source: str, at: int | None = None) -> None:
        at = time.time_ns() // 1_000_000 if at is None else at
        self.push(
            {"at": at, "time": log_stamp(at), "level": level, "app": app, "user": user,
             "message": message, "source": source}
        )  # fmt: skip

    def gateway(self, method: str, path: str, status: int, ms: int, app: str, user: str) -> None:
        level = "ERROR" if status >= 500 else "WARN" if status >= 400 else "INFO"
        self.add(level, app, user, f"[gateway] {method} {path} → {status} ({ms} ms)", "gateway")

    def recent(self, **filters: str) -> list[dict[str, Any]]:
        return [
            line
            for line in self.lines
            if all(not value or line.get(key) == value for key, value in filters.items())
        ]

    @staticmethod
    def matches(line: dict[str, Any], f: dict[str, str], now: int) -> bool:
        """Console filters: `app` (all/platform/id), `minutes`, `user` (substring), `level` and `source` (lists)."""
        app, user = f.get("app", ""), f.get("user", "").lower()
        mins = f.get("minutes", "")

        def listed(key: str) -> bool:
            wanted = [x.strip().lower() for x in f.get(key, "").split(",") if x.strip()]
            return not wanted or str(line.get(key, "")).lower() in wanted

        return bool(
            (app in ("", "all") or line["app"] == app)
            and (not mins.isdigit() or now - line["at"] <= int(mins) * 60_000)
            and (not user or user in line["user"].lower())
            and listed("level")
            and listed("source")
        )

    def query(self, f: dict[str, str]) -> list[dict[str, Any]]:
        now = time.time_ns() // 1_000_000
        return [x for x in self.lines if self.matches(x, f, now)]

    async def subscribe(self) -> AsyncGenerator[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(256)
        self.subscribers.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self.subscribers.discard(queue)


class OutboxIngest:
    """Durable dedupe/ack state; caller persists audit before recorded()."""

    def __init__(self, directory: Path) -> None:
        self.dir = directory
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    async def pending(self, app: str) -> list[str]:
        async with self._lock:
            return list(self._load(app).get("unacked", {}))

    async def recorded(self, app: str, ids: list[tuple[str, int]], new: int) -> None:
        async with self._lock:
            state = self._load(app)
            state["recorded"] = state.get("recorded", 0) + new
            now = time.time_ns() // 1_000_000
            pending = state.setdefault("unacked", {})
            for event_id, seq in ids:
                if _valid_event_id(event_id):
                    pending.setdefault(event_id, [seq, now])
            state["unacked"] = {k: v for k, v in pending.items() if now - v[1] < 7 * 86400_000}
            self._save(app, state)

    async def acked(self, app: str, ids: list[str]) -> None:
        async with self._lock:
            state = self._load(app)
            for item in ids:
                state.get("unacked", {}).pop(item, None)
            self._save(app, state)

    def _load(self, app: str) -> dict[str, Any]:
        path = self.dir / f"{app}.json"
        return cast(
            dict[str, Any],
            json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"recorded": 0, "unacked": {}},
        )

    def _save(self, app: str, data: dict[str, Any]) -> None:
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", app):
            raise ValueError("invalid app id")
        path = self.dir / f"{app}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, path)


def _valid_event_id(value: str) -> bool:
    return bool(value) and len(value) <= 80 and value.isascii() and value.isprintable()
