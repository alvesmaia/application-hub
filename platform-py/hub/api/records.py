"""Registros com validade (sessões MCP, confirmações, OAuth): tabela `records` do banco de estado quando há
`HUB_STATE_DB_URL` (várias réplicas) ou memória."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from hub.state import Change


def _now() -> int:
    return time.time_ns() // 1_000_000


_L = list[tuple[str, Any, int]]


class Records:
    def __init__(self, get_db: Callable[[], Any]) -> None:
        self._db = get_db
        self.mem: dict[tuple[str, str], tuple[Any, int]] = {}

    async def put(self, kind: str, key: str, v: Any, exp: int) -> None:
        if db := self._db():
            await db.rec_put(kind, key, v, exp)
            return
        if len(self.mem) > 50_000:
            now = _now()
            self.mem = {k: x for k, x in self.mem.items() if x[1] > now}
        self.mem[(kind, key)] = (v, exp)

    async def get(self, kind: str, key: str) -> Any | None:
        now = _now()
        if db := self._db():
            return await db.rec_get(kind, key, now)
        x = self.mem.get((kind, key))
        return x[0] if x and x[1] > now else None

    async def take(self, kind: str, key: str) -> Any | None:
        now = _now()
        if db := self._db():
            return await db.rec_take(kind, key, now)
        x = self.mem.pop((kind, key), None)
        return x[0] if x and x[1] > now else None

    async def delete(self, kind: str, key: str) -> None:
        if db := self._db():
            await db.rec_delete(kind, key)
        else:
            self.mem.pop((kind, key), None)

    async def del_where(self, kind: str, field: str, value: str) -> None:
        if db := self._db():
            await db.rec_delete_where(kind, field, value)
        else:
            self.mem = {k: x for k, x in self.mem.items() if not (k[0] == kind and x[0].get(field) == value)}

    @staticmethod
    def _is(v: Any, f: str, x: str) -> bool:
        cur = v.get(f)
        return (str(cur).lower() if isinstance(cur, bool) else cur) == x

    async def list(self, kind: str, flt: tuple[str, str] | None = None, n: int = 5000) -> _L:
        now = _now()
        if db := self._db():
            return list(await db.rec_list(kind, now, n, flt))
        out = [(k[1], x[0], x[1]) for k, x in self.mem.items()
               if k[0] == kind and x[1] > now and (not flt or self._is(x[0], *flt))]  # fmt: skip
        return sorted(out, key=lambda t: t[0])[:n]

    async def count(self, kind: str, flt: tuple[str, str] | None = None) -> int:
        if db := self._db():
            return int(await db.rec_count(kind, _now(), flt))
        return len(await self.list(kind, flt, 10**9))

    async def update(self, kind: str, key: str, fn: Callable[[Any | None], tuple[Change, Any]]) -> Any:
        """Mudança condicional atômica (linha travada no banco; laço único em memória)."""
        now = _now()
        if db := self._db():
            return await db.rec_update(kind, key, now, fn)
        x = self.mem.get((kind, key))
        change, out = fn(x[0] if x and x[1] > now else None)
        if change.action == "set":
            self.mem[(kind, key)] = (change.value, change.expires_at)
        elif change.action == "delete":
            self.mem.pop((kind, key), None)
        return out
