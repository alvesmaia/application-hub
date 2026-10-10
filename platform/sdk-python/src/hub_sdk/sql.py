"""Consultas SQL a conexões da plataforma (PostgreSQL, MySQL/MariaDB, SQL Server) — mesma API no hub e no dev.

.. code-block:: python

    vendas = sql.connection("vendas-bi")             # None se não vinculada (nem local)
    r = await vendas.query("select regiao, sum(valor) as total from vendas where ano = :ano group by 1",
                           {"ano": 2026})
    r.columns, r.rows, r.truncated, r.dicts()

- **No hub**: ``HUB_CONNECTIONS`` lista a conexão (``nome=postgres``…); a consulta vai pelo **broker**
  (``HUB_STORAGE_URL`` + ``HUB_STORAGE_TOKEN``, ``POST /_hub/sql/{nome}/query``). O app nunca vê a senha;
  o hub aplica somente leitura, limite de linhas/tempo e audita (sem dados).
- **No dev** (``hub dev``): ``HUB_SQL_LOCAL_FILE`` = ``.hub/connections.toml``::

      [connections.vendas-bi]
      url = "sqlite:///.hub/sample.db"     # ou postgresql://…, mysql+pymysql://… (driver no projeto)
      readonly = true
      max_rows = 5000

  (SQLAlchemy, extra ``hub-sdk[sql]``; parâmetros ``:nome`` iguais aos do broker.)
- **Banco do próprio app**: :func:`from_engine` (``hub_sdk.db.engine()``) com a mesma interface.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from typing_extensions import override

from hub_sdk.storage import StorageError, _call, _error, _retry_after, bound

__all__ = ["SqlConnection", "SqlError", "SqlResult", "connection", "from_engine", "local_connections"]

SQL_KINDS = ("postgres", "mysql", "sqlserver")
SqlError = StorageError  # mesmo formato (status HTTP + mensagem)

_READ_HEAD = re.compile(r"^\s*(?:--[^\n]*\n\s*|/\*.*?\*/\s*)*(select|with|values)\b", re.I | re.S)


@dataclass(frozen=True)
class SqlResult:
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool = False
    ms: int = 0

    def dicts(self) -> list[dict[str, Any]]:
        return [dict(zip(self.columns, r, strict=False)) for r in self.rows]

    def column(self, name: str) -> list[Any]:
        i = self.columns.index(name)
        return [r[i] for r in self.rows]


class SqlConnection:
    """Uma conexão SQL (use :func:`connection` ou :func:`from_engine`)."""

    def __init__(self, name: str, kind: str) -> None:
        self.name, self.kind = name, kind
        self.user: str | None = None
        self.user_sig: str | None = None

    def for_user(self, x_hub_user: str | None, x_hub_user_sig: str | None = None) -> SqlConnection:
        """Registra no hub o usuário da requisição como autor da consulta (com a assinatura do gateway)."""
        c = self._copy()
        c.user = x_hub_user or None
        c.user_sig = (x_hub_user_sig or None) if c.user is not None else None
        return c

    def _copy(self) -> SqlConnection:  # pragma: no cover — sobrescrito
        raise NotImplementedError

    async def query(
        self, sql: str, params: dict[str, Any] | None = None, max_rows: int | None = None
    ) -> SqlResult:
        raise NotImplementedError


class BrokerSql(SqlConnection):
    def __init__(self, name: str, kind: str, base: str, token: str) -> None:
        super().__init__(name, kind)
        self.base, self._token = base.rstrip("/"), token

    @override
    def __repr__(self) -> str:
        return f"BrokerSql({self.name!r}, {self.kind!r})"

    @override
    def _copy(self) -> SqlConnection:
        return BrokerSql(self.name, self.kind, self.base, self._token)

    @override
    async def query(
        self, sql: str, params: dict[str, Any] | None = None, max_rows: int | None = None
    ) -> SqlResult:
        h = {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}
        if self.user is not None:
            h["x-hub-user"] = self.user
            if self.user_sig is not None:
                h["x-hub-user-sig"] = self.user_sig
        body = json.dumps({"sql": sql, "params": params or {}, "max_rows": max_rows}).encode()
        url = f"{self.base}/_hub/sql/{quote(self.name, safe='')}/query"
        for attempt in range(1, 4):
            conn, resp = await _call("POST", url, h, body, timeout=330.0)
            try:
                raw = await asyncio.to_thread(resp.read)
            finally:
                conn.close()
            if resp.status == 200:
                d = json.loads(raw)
                return SqlResult(d["columns"], d["rows"], bool(d["truncated"]), int(d.get("ms", 0)))
            err = _error(resp.status, raw, _retry_after(resp))
            if err.status == 429 and attempt < 3:
                await asyncio.sleep(err.retry_after or 1)
                continue
            raise err
        raise StorageError(503, "banco indisponível", 2)  # pragma: no cover


class EngineSql(SqlConnection):
    """SQLAlchemy (banco local do dev ou o banco do próprio app)."""

    def __init__(self, name: str, engine: Any, readonly: bool = True, max_rows: int = 5000) -> None:
        super().__init__(name, "local")
        self.engine, self.readonly, self.max_rows = engine, readonly, max_rows

    @override
    def __repr__(self) -> str:
        return f"EngineSql({self.name!r})"

    @override
    def _copy(self) -> SqlConnection:
        return EngineSql(self.name, self.engine, self.readonly, self.max_rows)

    def _run(self, sql: str, params: dict[str, Any], limit: int) -> SqlResult:
        from sqlalchemy import text

        t0 = time.monotonic()
        with self.engine.connect() as c:
            res = c.execute(text(sql), params)
            if not res.returns_rows:  # escrita (só com readonly = false no connections.toml)
                c.commit()
                return SqlResult([], [], False, int((time.monotonic() - t0) * 1000))
            cols = list(res.keys())
            rows = [list(r) for r in res.fetchmany(limit + 1)]
            c.rollback()
        ms = int((time.monotonic() - t0) * 1000)
        return SqlResult(cols, [[_plain(v) for v in r] for r in rows[:limit]], len(rows) > limit, ms)

    @override
    async def query(
        self, sql: str, params: dict[str, Any] | None = None, max_rows: int | None = None
    ) -> SqlResult:
        if self.readonly and (not _READ_HEAD.match(sql) or ";" in sql.strip().rstrip(";")):
            raise StorageError(403, "Conexão somente leitura: use só SELECT/WITH (uma instrução).")
        limit = min(max(1, max_rows or self.max_rows), self.max_rows)
        try:
            return await asyncio.to_thread(self._run, sql, params or {}, limit)
        except StorageError:
            raise
        except Exception as e:  # erro do banco: mesma forma do broker
            raise StorageError(502, f"Banco recusou a consulta: {str(e).splitlines()[0][:200]}") from e


def _plain(v: Any) -> Any:
    import datetime as dt
    import decimal

    if isinstance(v, decimal.Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    if isinstance(v, dt.date | dt.datetime | dt.time):
        return v.isoformat()
    if isinstance(v, bytes | bytearray | memoryview):
        return None
    return v


def from_engine(engine: Any, name: str = "app", readonly: bool = True, max_rows: int = 5000) -> SqlConnection:
    """O banco do próprio app (``hub_sdk.db.engine()``) com a interface de consultas dos dashboards."""
    return EngineSql(name, engine, readonly, max_rows)


def local_connections(path: str | os.PathLike[str] | None = None) -> dict[str, dict[str, Any]]:
    """``[connections.<nome>]`` de ``.hub/connections.toml`` (``HUB_SQL_LOCAL_FILE``)."""
    p = Path(path or os.environ.get("HUB_SQL_LOCAL_FILE", "") or "")
    if not str(path or os.environ.get("HUB_SQL_LOCAL_FILE", "")) or not p.is_file():
        return {}
    doc = tomllib.loads(p.read_text(encoding="utf-8"))
    out = doc.get("connections", {})
    return {str(k): dict(v) for k, v in out.items() if isinstance(v, dict) and v.get("url")}


_engines: dict[str, Any] = {}


def _local(name: str, cfg: dict[str, Any], base: Path) -> SqlConnection:
    from sqlalchemy import create_engine

    url = str(cfg["url"])
    if url.startswith("sqlite:///") and not url.startswith("sqlite:////"):
        rel = url[len("sqlite:///") :]
        if rel and not Path(rel).is_absolute():
            url = f"sqlite:///{(base / rel).resolve().as_posix()}"
    if url not in _engines:
        _engines[url] = create_engine(url)
    return EngineSql(name, _engines[url], bool(cfg.get("readonly", True)), int(cfg.get("max_rows", 5000)))


def connection(name: str) -> SqlConnection | None:
    """Conexão SQL ``name``: local (``HUB_SQL_LOCAL_FILE``) no dev; pelo broker se vinculada no hub."""
    file = os.environ.get("HUB_SQL_LOCAL_FILE", "").strip()
    if file:
        cfg = local_connections(file).get(name)
        # caminhos relativos do sqlite valem a partir da raiz do projeto (pai de .hub/)
        return _local(name, cfg, Path(file).resolve().parent.parent) if cfg is not None else None
    kind = next((k for n, k in bound() if n == name and k in SQL_KINDS), None)
    base = os.environ.get("HUB_STORAGE_URL", "")
    token = os.environ.get("HUB_STORAGE_TOKEN", "")
    if kind is None or not base or len(token) < 16:
        return None
    return BrokerSql(name, kind, base, token)
