"""Read accounts for the apps' Postgres (dashboards, SQL clients): the operations behind Administration › Users ›
Database access. SQL in `dbaccess_sql`, network/TLS in `dbaccess_net`; `run(db, statements)` is the psql of the
database container (tests pass a disposable Postgres)."""

from __future__ import annotations

import datetime
import json
import secrets
from collections.abc import Awaitable, Callable
from typing import Any

from hub.admin import dbaccess_net as net
from hub.admin import dbaccess_sql as q

Run = Callable[[str, list[str]], Awaitable[str]]


class DbAccess:
    def __init__(self, run: Run, hub_db: str | None = None) -> None:
        self.run, self.hub_db = run, hub_db or net.hub_db()

    async def _json(self, db: str, sql: str) -> Any:
        out = (await self.run(db, [sql])).strip().splitlines()
        return json.loads(out[-1] if out else "null")

    async def _readable(self) -> list[str]:
        return [d for d in await self._json("postgres", q.DBS_SQL) if d not in net.NEVER]

    async def databases(self) -> list[str]:
        """The apps' databases (the hub's own is a separate choice: `has_hub_db`)."""
        return [d for d in await self._readable() if d != self.hub_db]

    async def has_hub_db(self) -> bool:
        return self.hub_db in await self._readable()

    async def accounts(self, now: datetime.datetime | None = None) -> list[dict[str, Any]]:
        await self.run("postgres", q.readers_sql())
        now = now or datetime.datetime.now(datetime.UTC)
        return [_row(r, now) for r in await self._json("postgres", q.LIST_SQL)]

    async def create(self, a: q.NewAccount, now: datetime.datetime | None = None) -> tuple[str, str]:
        """(role, password): the password exists only in this answer."""
        role = q.role_of(a.name)
        q.check_dbs(a.dbs)
        if missing := sorted(set(a.dbs) - set(await self._readable())):
            raise ValueError(f"bancos inexistentes: {', '.join(missing)}")
        if not 1 <= a.days <= 365:
            raise ValueError(f"validade de {a.days} dias: use de 1 a 365")
        now = now or datetime.datetime.now(datetime.UTC)
        await self.run("postgres", q.readers_sql())
        for db in a.dbs:
            await self.run("postgres", q.group_cluster_sql(db))
            await self.run(db, q.group_db_sql(db))
        password = secrets.token_urlsafe(24)
        until = (now + datetime.timedelta(days=a.days)).isoformat()
        await self.run("postgres", q.create_sql(a, password, until, now.isoformat()))
        return role, password

    async def new_password(self, role: str) -> str:
        await self._known(role)
        password = secrets.token_urlsafe(24)
        await self.run("postgres", q.password_sql(role, password))
        return password

    async def remove(self, role: str) -> None:
        await self._known(role)
        await self.run("postgres", q.drop_sql(role))

    async def suspend_owner(self, uid: str) -> list[str]:
        """The responsible person left (deactivated, or every access revoked): their accounts stop now."""
        roles: list[str] = [str(a["role"]) for a in await self.accounts() if a["owner"] == uid and a["login"]]
        for role in roles:
            await self.run("postgres", q.disable_sql(role))
        return roles

    async def _known(self, role: str) -> dict[str, Any]:
        found = next((a for a in await self.accounts() if a["role"] == role), None)
        if found is None:
            raise LookupError(f"conta de leitura não encontrada: {role}")
        return found


def _row(r: dict[str, Any], now: datetime.datetime) -> dict[str, Any]:
    try:
        meta = json.loads(r.get("meta") or "{}")
    except ValueError:
        meta = {}
    until = datetime.datetime.fromisoformat(r["until"]) if r.get("until") else None
    expired = until is not None and until <= now
    state = "expirada" if expired else ("ativa" if r["login"] else "suspensa")
    return {"role": r["role"], "login": bool(r["login"]), "until": until, "state": state,
            "owner": meta.get("owner", ""), "dbs": meta.get("dbs", []), "by": meta.get("by", ""), "at": meta.get("at", "")}  # fmt: skip
