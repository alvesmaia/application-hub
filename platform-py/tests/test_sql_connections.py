"""Conexões SQL (#21): validação, guarda de somente leitura, parâmetros, broker `/_hub/sql/*` e auditoria
sem dados. O banco é um SQLite em memória no lugar do driver; com `HUB_DB_TEST_URL`, também um Postgres real."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, cast, override
from urllib.parse import urlsplit

import pytest
from admin_helper import make_hub
from fastapi.testclient import TestClient

from hub.connections import broker as broker_mod
from hub.connections.sql import QueryResult, SqlBackend, SqlConfig, SqlError, bind, check_sql, jsonable
from hub.connections.store import Backend, ConnStore

PASSWORD = "pg-pass-SUPER-9911"
PG = {
    "name": "vendas-bi",
    "kind": "postgres",
    "host": "db.empresa.local",
    "database": "vendas",
    "username": "leitor",
    "secret": PASSWORD,
    "bindings": [{"app": "ativos", "env": "producao"}],
}


def run(coro: Any) -> Any:
    return asyncio.run(coro)


class SqliteBackend(SqlBackend):
    """Mesmas regras do SqlBackend (guarda, limites, serialização), executando num SQLite em memória."""

    def __init__(self, cfg: SqlConfig) -> None:
        super().__init__(cfg)
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.db.executescript(
            "CREATE TABLE vendas(id INTEGER, regiao TEXT, valor REAL);"
            + "".join(f"INSERT INTO vendas VALUES({i}, '{'NS'[i % 2]}', {i * 1.5});" for i in range(1, 21))
        )

    @override
    async def _run(self, sql: str, params: dict[str, Any], fetch: int) -> tuple[list[str], list[Any]]:
        q, values = bind(sql, params, "qmark")
        cur = self.db.execute(q, values)
        return [d[0] for d in cur.description or []], [list(r) for r in cur.fetchmany(fetch)]


@pytest.fixture
def hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    return make_hub(tmp_path, monkeypatch)


def fake(hub: TestClient, name: str) -> SqliteBackend:
    store = cast(ConnStore, hub.app.state.conns)  # type: ignore[attr-defined]
    c = store.get(name)
    assert c is not None
    b = SqliteBackend(cast(SqlBackend, store.backend(c)).cfg)
    store.clients[name] = (c["rev"], cast(Backend, b))
    return b


def token_of(hub: TestClient) -> str:
    return str(run(hub.app.state.admin.cfg.managed_var("HUB_STORAGE_TOKEN_ATIVOS_PRODUCAO"))["value"])  # type: ignore[attr-defined]


# ---- regras puras -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "select 1",
        "WITH x AS (SELECT 1) SELECT * FROM x;",
        "select 'drop table x; delete' as t",
        "select a -- update\n from t",
        'select "insert" from t',
        "select $$; delete$$",
    ],
)
def test_readonly_guard_accepts_reads(sql: str) -> None:
    assert check_sql(sql, True)


@pytest.mark.parametrize(
    ("sql", "status"),
    [
        ("delete from t", 403),
        ("select 1; drop table t", 400),
        ("select * into novo from t", 403),
        ("with x as (delete from t returning *) select * from x", 403),
        ("select pg_sleep(10)", 403),
        ("exec sp_who", 403),
        ("select 1 /* sem fim", 400),
        ("select 'aberto", 400),
        ("", 400),
        ("select 1 for update", 403),
    ],
)
def test_readonly_guard_rejects(sql: str, status: int) -> None:
    with pytest.raises(SqlError) as e:
        check_sql(sql, True)
    assert e.value.status == status


def test_readwrite_allows_writes_but_one_statement() -> None:
    assert check_sql("update t set a = 1", False)
    with pytest.raises(SqlError):
        check_sql("update t set a = 1; select 1", False)


def test_bind_styles_and_errors() -> None:
    sql = "select ':x', a::text from t where r = :r and v > :v and r2 = :r -- :nada\n and p like '10%'"
    q, v = bind(sql, {"r": "N", "v": 2}, "dollar")
    assert q.count("$1") == 2 and "$2" in q and "':x'" in q and "::text" in q and v == ["N", 2]
    q, v = bind("select * from t where a = :a and b like '%x'", {"a": 1}, "format")
    assert q == "select * from t where a = %s and b like '%%x'" and v == [1]
    q, v = bind("select :a, :a", {"a": 1}, "qmark")
    assert q == "select ?, ?" and v == [1, 1]
    with pytest.raises(SqlError, match="sem valor"):
        bind("select :a", {}, "qmark")
    with pytest.raises(SqlError, match="Parâmetro"):
        bind("select :a", {"a": [1]}, "qmark")
    with pytest.raises(SqlError, match="inválido"):
        bind("select 1", {"a b": 1}, "qmark")


def test_jsonable() -> None:
    import datetime as dt
    import decimal
    import uuid

    assert jsonable(decimal.Decimal("3")) == 3 and jsonable(decimal.Decimal("1.5")) == 1.5
    assert jsonable(dt.date(2026, 1, 2)) == "2026-01-02" and jsonable(b"x") is None
    assert jsonable(float("nan")) is None and jsonable(uuid.UUID(int=1)).startswith("0000")
    assert jsonable(dt.timedelta(seconds=2)) == 2.0 and jsonable(object()).startswith("<")


# ---- conexões e broker --------------------------------------------------------------------------


def test_sql_connection_crud_masks_password(hub: TestClient, tmp_path: Path) -> None:
    r = hub.post("/api/admin/connections", json=PG)
    assert r.status_code == 201, r.text
    v = r.json()
    assert (
        v["kind"] == "postgres" and v["port"] == 5432 and v["readonly"] is True and v["secret"] == "••••••••"
    )
    assert v["allowed_ops"] == ["query"] and v["max_rows"] == 5000
    page = hub.get("/admin/connections").text
    assert "PostgreSQL" in page and "db.empresa.local:5432 · vendas" in page and PASSWORD not in page
    assert 'data-conn-new="sqlserver"' in page
    on_disk = (tmp_path / "connections.json").read_text(encoding="utf-8")
    assert PASSWORD not in on_disk
    assert PASSWORD not in json.dumps(hub.get("/api/admin/connections").json())
    audit = json.dumps(run(hub.app.state.admin.cfg.read())["audit"])  # type: ignore[attr-defined]
    assert PASSWORD not in audit
    upd = hub.put(
        "/api/admin/connections/vendas-bi", json={**PG, "secret": "", "readonly": False, "max_rows": 10}
    )
    assert upd.status_code == 200 and upd.json()["readonly"] is False and upd.json()["max_rows"] == 10


@pytest.mark.parametrize(
    ("patch", "msg"),
    [
        ({"host": "bad host!"}, "Servidor"),
        ({"port": 70000}, "Porta"),
        ({"database": ""}, "banco"),
        ({"secret": ""}, "senha"),
        ({"ssl": "x"}, "TLS"),
        ({"max_rows": 0}, "linhas"),
        ({"timeout_s": 999}, "Tempo"),
    ],
)
def test_sql_validation(hub: TestClient, patch: dict[str, Any], msg: str) -> None:
    r = hub.post("/api/admin/connections", json={**PG, **patch})
    assert r.status_code == 400 and msg in r.json()["error"], r.text


def test_broker_sql_query_limits_and_audit_without_data(hub: TestClient) -> None:
    hub.post("/api/admin/connections", json={**PG, "max_rows": 5})
    hub.post(
        "/api/admin/connections",
        json={**PG, "name": "outro", "bindings": [{"app": "coletor", "env": "producao"}]},
    )
    fake(hub, "vendas-bi")
    b = TestClient(broker_mod.build_broker(hub.app.state.conns, hub.app.state.admin, b"k" * 32))  # type: ignore[attr-defined]
    auth = {"authorization": f"Bearer {token_of(hub)}"}
    listed = b.get("/_hub/storage", headers=auth).json()["connections"]
    assert listed == [
        {"name": "vendas-bi", "kind": "postgres", "ops": ["query"], "max_rows": 5, "readonly": True}
    ]
    q = {"sql": "select regiao, sum(valor) as total from vendas where id > :min group by regiao order by regiao",
         "params": {"min": 2}}  # fmt: skip
    r = b.post("/_hub/sql/vendas-bi/query", json=q, headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["columns"] == ["regiao", "total"] and [x[0] for x in body["rows"]] == ["N", "S"]
    assert body["truncated"] is False and r.headers["cache-control"] == "no-store"
    many = b.post("/_hub/sql/vendas-bi/query", json={"sql": "select * from vendas"}, headers=auth).json()
    assert len(many["rows"]) == 5 and many["truncated"] is True
    two = b.post(
        "/_hub/sql/vendas-bi/query", json={"sql": "select * from vendas", "max_rows": 2}, headers=auth
    )
    assert len(two.json()["rows"]) == 2
    assert (
        b.post("/_hub/sql/vendas-bi/query", json={"sql": "delete from vendas"}, headers=auth).status_code
        == 403
    )
    assert b.post("/_hub/sql/vendas-bi/query", json={"x": 1}, headers=auth).status_code == 400
    assert b.post("/_hub/sql/outro/query", json=q, headers=auth).status_code == 404  # vinculada a outro app
    assert b.post("/_hub/sql/vendas-bi/query", json=q).status_code == 401
    # conexão SQL não serve para operações de arquivo
    assert b.get("/_hub/storage/vendas-bi/list", headers=auth).status_code == 403
    audit = run(hub.app.state.admin.cfg.read())["audit"]  # type: ignore[attr-defined]
    mine = [e for e in audit if "SQL" in e["title"] or "consulta SQL" in e["title"]]
    assert any(e["title"] == "Consulta SQL" and "truncada" in e["detail"] for e in mine)
    assert any("falhou" in e["title"] for e in mine)
    blob = json.dumps(mine)
    assert (
        "regiao" not in blob and "vendas where" not in blob and '"min"' not in blob and PASSWORD not in blob
    )


def test_sql_connection_test_steps(hub: TestClient) -> None:
    hub.post("/api/admin/connections", json=PG)
    fake(hub, "vendas-bi")

    async def boom(*_a: Any) -> Any:
        raise OSError("connection refused password=abc")

    r = hub.post("/api/admin/connections/vendas-bi/test")
    assert r.status_code == 200 and r.json()["steps"][0]["key"] == "sql_connect"
    store = cast(ConnStore, hub.app.state.conns)  # type: ignore[attr-defined]
    b = cast(SqliteBackend, store.clients["vendas-bi"][1])
    b.__dict__["_run"] = boom
    r = hub.post("/api/admin/connections/vendas-bi/test").json()
    assert r["ok"] is False and "abc" not in json.dumps(r)


def test_query_timeout_and_driver_error() -> None:
    b = SqliteBackend(SqlConfig("postgres", "h", 1, "d", "u", "p", timeout_s=1))

    async def slow(*_a: Any) -> Any:
        await asyncio.sleep(5)

    async def bad(*_a: Any) -> Any:
        raise RuntimeError("syntax error near x")

    b.__dict__["_run"] = slow
    with pytest.raises(SqlError) as e:
        run(b.query("select 1"))
    assert e.value.status == 504
    b.__dict__["_run"] = bad
    with pytest.raises(SqlError) as e:
        run(b.query("select 1"))
    assert e.value.status == 502 and "syntax" in e.value.message
    assert "p" not in repr(b).split("/")[-1] or "SqlBackend(" in repr(b)


# ---- Postgres real (descartável) ----------------------------------------------------------------

PG_URL = os.getenv("HUB_DB_TEST_URL")


@pytest.mark.skipif(not PG_URL, reason="HUB_DB_TEST_URL not set")
def test_real_postgres_readonly_transaction() -> None:
    u = urlsplit(str(PG_URL))
    cfg = SqlConfig(
        "postgres", u.hostname or "", u.port or 5432, u.path.lstrip("/"), u.username or "", u.password or "",
        ssl="disable", max_rows=3, timeout_s=5,
    )  # fmt: skip

    async def go() -> tuple[QueryResult, list[dict[str, Any]], SqlError]:
        b = SqlBackend(cfg)
        try:
            r = await b.query(
                "select g, g * 2 as dobro, now() as quando from generate_series(1, :n) g", {"n": 10}
            )
            steps = await b.test()
            rw = SqlBackend(dataclasses.replace(cfg, readonly=False))
            # mesmo sem a guarda léxica, a transação READ ONLY recusa escrita
            rw.cfg = dataclasses.replace(cfg, readonly=True)
            try:
                await rw._run("create table hub_sql_ro_test(a int)", {}, 1)
                err = SqlError("escreveu")
            except Exception as e:  # asyncpg ReadOnlySQLTransactionError
                err = SqlError(type(e).__name__)
            await rw.close()
            return r, steps, err
        finally:
            await b.close()

    r, steps, err = run(go())
    assert r.columns == ["g", "dobro", "quando"] and r.rows[0][:2] == [1, 2] and r.truncated is True
    assert isinstance(r.rows[0][2], str) and len(r.rows) == 3
    assert steps[0]["ok"] is True and "PostgreSQL" in steps[0]["detail"]
    assert "ReadOnly" in err.message
