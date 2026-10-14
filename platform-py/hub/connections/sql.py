"""Conexões SQL (PostgreSQL, MySQL/MariaDB, SQL Server) do broker: consultas dos apps com a credencial do hub.

O app manda ``{"sql", "params"}`` (parâmetros nomeados ``:nome``); o hub converte para o estilo do driver,
aplica somente leitura (transação READ ONLY onde o banco permite + guarda léxica sempre), limite de linhas
e de tempo, e devolve ``{"columns", "rows", "truncated", "ms"}``. Nada de dados nem valores de parâmetros
em log/auditoria: só o hash do SQL, o tamanho e a contagem de linhas."""

from __future__ import annotations

import asyncio
import datetime as dt
import decimal
import hashlib
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, override

SQL_KINDS = ("postgres", "mysql", "sqlserver")
KIND_LABEL = {"postgres": "PostgreSQL", "mysql": "MySQL / MariaDB", "sqlserver": "SQL Server"}
DEFAULT_PORT = {"postgres": 5432, "mysql": 3306, "sqlserver": 1433}
MAX_ROWS_CAP = 50_000
MAX_TIMEOUT_S = 300
MAX_SQL = 20_000
MAX_PARAMS = 100

_WRITE = re.compile(
    r"\b(insert|update|delete|merge|upsert|drop|alter|create|truncate|grant|revoke|exec|execute|call|copy|"
    r"lock|vacuum|analyze|reindex|cluster|comment|attach|detach|handler|rename|set|reset|"
    r"begin|commit|rollback|savepoint|prepare|deallocate|do|declare|into|outfile|dumpfile|shutdown|kill|"
    r"backup|restore|dbcc|bulk|openrowset|opendatasource|openquery|xp_\w*|sp_\w*|pg_sleep|"
    r"pg_terminate_backend|pg_cancel_backend|lo_import|lo_export|dblink\w*|waitfor|sleep|benchmark)\b",
    re.I,
)


class SqlError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _strip(sql: str) -> str:
    """SQL sem comentários e com literais/identificadores entre aspas trocados por marcadores."""
    out: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        if sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j < 0 else j
        elif sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            if j < 0:
                raise SqlError("SQL: comentário /* sem fechamento.")
            i = j + 2
            out.append(" ")
        elif c in "'\"`[":
            end = {"'": "'", '"': '"', "`": "`", "[": "]"}[c]
            j = i + 1
            while True:
                j = sql.find(end, j)
                if j < 0:
                    raise SqlError("SQL: texto entre aspas sem fechamento.")
                if end != "]" and sql.startswith(end * 2, j):
                    j += 2
                    continue
                break
            out.append("''" if c == "'" else '""')
            i = j + 1
        elif c == "$" and (m := re.match(r"\$([A-Za-z_]\w*)?\$", sql[i:])):
            tag = m.group(0)
            j = sql.find(tag, i + len(tag))
            if j < 0:
                raise SqlError("SQL: texto $$ sem fechamento.")
            out.append("''")
            i = j + len(tag)
        else:
            out.append(c)
            i += 1
    return "".join(out)


def check_sql(sql: str, readonly: bool) -> str:
    """Uma única instrução; somente leitura = SELECT/WITH sem palavras de escrita (além da transação RO)."""
    if not isinstance(sql, str) or not sql.strip():
        raise SqlError("Informe o SQL.")
    if len(sql) > MAX_SQL:
        raise SqlError(f"SQL acima de {MAX_SQL} caracteres.")
    if "\x00" in sql:
        raise SqlError("SQL inválido.")
    bare = _strip(sql).strip().rstrip(";").strip()
    if ";" in bare:
        raise SqlError("Uma instrução por consulta (sem ';' no meio).")
    if readonly:
        head = (bare.split(None, 1) or [""])[0].lower()
        if head not in ("select", "with", "values", "table") or _WRITE.search(bare):
            raise SqlError("Conexão somente leitura: use só SELECT/WITH (sem escrita nem comandos).", 403)
    return sql.strip().rstrip(";").strip()


def bind(sql: str, params: dict[str, Any], style: str) -> tuple[str, list[Any]]:
    """``:nome`` → ``$1`` (postgres), ``%s`` (mysql/sqlserver) ou ``?`` (sqlite), fora de aspas e comentários."""
    if len(params) > MAX_PARAMS:
        raise SqlError(f"Até {MAX_PARAMS} parâmetros.")
    for k, v in params.items():
        if not re.fullmatch(r"[A-Za-z_]\w{0,62}", str(k)):
            raise SqlError("Nome de parâmetro inválido.")
        if v is not None and not isinstance(v, str | int | float | bool):
            raise SqlError(f"Parâmetro {k}: use texto, número, booleano ou nulo.")
        if isinstance(v, str) and len(v) > 4000:
            raise SqlError(f"Parâmetro {k}: até 4000 caracteres.")
    out: list[str] = []
    values: list[Any] = []
    order: dict[str, int] = {}

    def esc(part: str) -> str:  # drivers "format" aplicam `%` na consulta inteira, inclusive em literais
        return part.replace("%", "%%") if style == "format" else part

    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        if sql.startswith("--", i):
            j = sql.find("\n", i)
            j = n if j < 0 else j
            out.append(esc(sql[i:j]))
            i = j
        elif sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append(esc(sql[i:j]))
            i = j
        elif c in "'\"`[":
            end = {"'": "'", '"': '"', "`": "`", "[": "]"}[c]
            j = i + 1
            while True:
                j = sql.find(end, j)
                if j < 0:
                    j = n
                    break
                if end != "]" and sql.startswith(end * 2, j):
                    j += 2
                    continue
                j += 1
                break
            out.append(esc(sql[i:j]))
            i = j
        elif c == ":" and sql.startswith("::", i):
            out.append("::")
            i += 2
        elif c == ":" and (m := re.match(r":([A-Za-z_]\w*)", sql[i:])):
            name = m.group(1)
            if name not in params:
                raise SqlError(f"Parâmetro :{name} sem valor.")
            if style == "dollar":
                if name not in order:
                    values.append(params[name])
                    order[name] = len(values)
                out.append(f"${order[name]}")
            else:
                values.append(params[name])
                out.append("%s" if style == "format" else "?")
            i += len(m.group(0))
        elif c == "%" and style == "format":
            out.append("%%")
            i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out), values


def jsonable(v: Any) -> Any:
    if v is None or isinstance(v, bool | int | str):
        return v
    if isinstance(v, float):
        return v if v == v and v not in (float("inf"), float("-inf")) else None
    if isinstance(v, decimal.Decimal):
        return int(v) if v == v.to_integral_value() and abs(v) < 2**53 else float(v)
    if isinstance(v, dt.datetime | dt.date | dt.time):
        return v.isoformat()
    if isinstance(v, dt.timedelta):
        return v.total_seconds()
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, bytes | bytearray | memoryview):
        return None  # binários não saem pelo broker
    return str(v)


def sql_hash(sql: str) -> str:
    return hashlib.sha256(sql.encode()).hexdigest()[:12]


@dataclass(frozen=True)
class SqlConfig:
    kind: str
    host: str
    port: int
    database: str
    username: str
    password: str
    ssl: str = "prefer"  # disable | prefer | require
    readonly: bool = True
    max_rows: int = 5000
    timeout_s: int = 30


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool
    ms: int

    def json(self) -> dict[str, Any]:
        return {"columns": self.columns, "rows": self.rows, "truncated": self.truncated, "ms": self.ms}


class SqlBackend:
    """Executa consultas com a credencial guardada. Um pool por conexão (Postgres) ou conexão por consulta."""

    def __init__(self, cfg: SqlConfig) -> None:
        self.cfg = cfg
        self._pool: Any = None
        self._pool_lock = asyncio.Lock()

    @override
    def __repr__(self) -> str:  # nunca a senha
        return f"SqlBackend({self.cfg.kind}://{self.cfg.host}:{self.cfg.port}/{self.cfg.database})"

    async def query(
        self, sql: str, params: dict[str, Any] | None = None, max_rows: int | None = None
    ) -> QueryResult:
        clean = check_sql(sql, self.cfg.readonly)
        limit = min(max(1, max_rows or self.cfg.max_rows), self.cfg.max_rows, MAX_ROWS_CAP)
        t0 = time.monotonic()
        try:
            cols, rows = await asyncio.wait_for(
                self._run(clean, params or {}, limit + 1), self.cfg.timeout_s + 2
            )
        except TimeoutError as e:
            raise SqlError(f"Consulta passou do limite de {self.cfg.timeout_s} s.", 504) from e
        except SqlError:
            raise
        except Exception as e:  # erro do driver: mensagem curta, sem SQL nem valores
            raise SqlError(f"Banco recusou a consulta: {driver_message(e)}", 502) from e
        ms = int((time.monotonic() - t0) * 1000)
        truncated = len(rows) > limit
        return QueryResult(cols, [[jsonable(v) for v in r] for r in rows[:limit]], truncated, ms)

    async def test(self) -> list[dict[str, Any]]:
        steps: list[dict[str, Any]] = []
        t0 = time.monotonic()
        try:
            r = await asyncio.wait_for(
                self._run(VERSION_SQL[self.cfg.kind], {}, 1), min(self.cfg.timeout_s, 15) + 2
            )
            version = str(r[1][0][0]) if r[1] and r[1][0] else ""
            steps.append({"key": "sql_connect", "ok": True, "detail": version[:120], "ms": _ms(t0)})
        except Exception as e:
            steps.append({"key": "sql_connect", "ok": False, "detail": driver_message(e), "ms": _ms(t0)})
            steps.append({"key": "sql_readonly", "ok": None, "detail": "", "ms": 0})
            return steps
        steps.append(
            {
                "key": "sql_readonly",
                "ok": True,
                "detail": "somente leitura" if self.cfg.readonly else "leitura e escrita",
                "ms": 0,
            }
        )
        return steps

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def _run(self, sql: str, params: dict[str, Any], fetch: int) -> tuple[list[str], list[Any]]:
        k = self.cfg.kind
        if k == "postgres":
            return await self._pg(sql, params, fetch)
        if k == "mysql":
            return await asyncio.to_thread(self._mysql, sql, params, fetch)
        if k == "sqlserver":
            return await asyncio.to_thread(self._mssql, sql, params, fetch)
        raise SqlError("Tipo de banco desconhecido.")  # pragma: no cover

    async def _pg(self, sql: str, params: dict[str, Any], fetch: int) -> tuple[list[str], list[Any]]:
        import asyncpg

        async with self._pool_lock:
            if self._pool is None:
                ssl: Any = {"disable": False, "require": "require", "prefer": "prefer"}[self.cfg.ssl]
                self._pool = await asyncpg.create_pool(
                    host=self.cfg.host,
                    port=self.cfg.port,
                    user=self.cfg.username,
                    password=self.cfg.password,
                    database=self.cfg.database,
                    ssl=ssl,
                    min_size=0,
                    max_size=4,
                    timeout=min(self.cfg.timeout_s, 15),
                    command_timeout=self.cfg.timeout_s + 1,
                    server_settings={"application_name": "app-hub-broker"},
                )
        q, values = bind(sql, params, "dollar")
        async with self._pool.acquire() as conn:
            tx = conn.transaction(readonly=self.cfg.readonly)
            await tx.start()
            try:
                await conn.execute(f"SET LOCAL statement_timeout = {int(self.cfg.timeout_s * 1000)}")
                stmt = await conn.prepare(q)
                cols = [a.name for a in stmt.get_attributes()]
                if cols:
                    rows = await (await stmt.cursor(*values)).fetch(fetch)
                else:  # escrita/comando: sem linhas
                    await stmt.fetch(*values)
                    rows = []
            except BaseException:
                await tx.rollback()
                raise
            # somente leitura: nada fica; leitura e escrita: confirma
            await (tx.rollback() if self.cfg.readonly else tx.commit())
        return cols, [list(r.values()) for r in rows]

    def _mysql(self, sql: str, params: dict[str, Any], fetch: int) -> tuple[list[str], list[Any]]:
        import pymysql

        ssl = None if self.cfg.ssl == "disable" else {"ssl": {}} if self.cfg.ssl == "require" else None
        conn = pymysql.connect(
            host=self.cfg.host,
            port=self.cfg.port,
            user=self.cfg.username,
            password=self.cfg.password,
            database=self.cfg.database,
            connect_timeout=min(self.cfg.timeout_s, 15),
            read_timeout=self.cfg.timeout_s + 1,
            charset="utf8mb4",
            autocommit=False,
            ssl=ssl["ssl"] if ssl else None,
        )
        try:
            with conn.cursor() as cur:
                cur.execute(f"SET SESSION max_execution_time = {int(self.cfg.timeout_s * 1000)}")
                cur.execute("START TRANSACTION READ ONLY" if self.cfg.readonly else "START TRANSACTION")
                q, values = bind(sql, params, "format")
                cur.execute(q, values)
                cols = [d[0] for d in (cur.description or [])]
                rows = [list(r) for r in cur.fetchmany(fetch)] if cols else []
            conn.rollback() if self.cfg.readonly else conn.commit()
            return cols, rows
        finally:
            conn.close()

    def _mssql(self, sql: str, params: dict[str, Any], fetch: int) -> tuple[list[str], list[Any]]:
        import pytds

        conn = pytds.connect(
            server=self.cfg.host,
            port=self.cfg.port,
            database=self.cfg.database,
            user=self.cfg.username,
            password=self.cfg.password,
            timeout=self.cfg.timeout_s,
            login_timeout=min(self.cfg.timeout_s, 15),
            autocommit=False,
            appname="app-hub-broker",
        )
        try:
            with conn.cursor() as cur:
                q, values = bind(sql, params, "format")
                cur.execute(q, tuple(values))
                cols = [d[0] for d in (cur.description or [])]
                rows = [list(r) for r in cur.fetchmany(fetch)] if cols else []
            # sem transação só de leitura no SQL Server: no modo leitura, o rollback desfaz qualquer escrita
            conn.rollback() if self.cfg.readonly else conn.commit()
            return cols, rows
        finally:
            conn.close()


VERSION_SQL = {"postgres": "SELECT version()", "mysql": "SELECT version()", "sqlserver": "SELECT @@VERSION"}


def driver_message(e: BaseException) -> str:
    msg = str(e).splitlines()[0] if str(e) else type(e).__name__
    msg = re.sub(r"(password|pwd|senha)\s*[=:]\s*\S+", r"\1=***", msg, flags=re.I)
    return "".join(c for c in msg if c.isprintable())[:200]


def _ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)


__all__ = [
    "DEFAULT_PORT",
    "KIND_LABEL",
    "SQL_KINDS",
    "QueryResult",
    "SqlBackend",
    "SqlConfig",
    "SqlError",
    "bind",
    "check_sql",
    "sql_hash",
]
