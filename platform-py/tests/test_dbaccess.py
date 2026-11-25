"""Read access to the apps' Postgres from other machines: the generated `pg_hba.conf`, the TLS certificate, and the
read accounts against a real (disposable) Postgres."""

from __future__ import annotations

import datetime
import json
import os
import secrets
from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import pytest
from admin_helper import add_user, make_hub

from hub.admin import dbaccess_net as net
from hub.admin import dbaccess_sql as q
from hub.admin.dbaccess import DbAccess

DB_URL = os.getenv("HUB_DB_TEST_URL")
pg = pytest.mark.skipif(not DB_URL, reason="HUB_DB_TEST_URL not set")
NETS = [("10.231.1.0/24", "10.231.1.1"), ("172.20.0.0/16", "172.20.0.1")]


def cfg(**env: str) -> net.DbAccessCfg:
    return net.from_env({"HUB_PUBLIC_URL": "https://hub.empresa.com", **env})


def test_off_keeps_the_image_default() -> None:
    text = net.hba(cfg(), NETS)
    assert text.splitlines()[-1] == "host all all all scram-sha-256" and "hub_readers" not in text


def test_on_lets_only_readers_in_over_tls_from_the_allowed_ranges() -> None:
    c = cfg(HUB_DB_PUBLIC="1", HUB_DB_ALLOWED_CIDRS="10.0.0.0/8, 192.168.10.7 ,lixo")
    assert (
        c.cidrs == ("10.0.0.0/8", "192.168.10.7/32")
        and c.bad_cidrs == ("lixo",)
        and c.host == "hub.empresa.com"
    )
    lines = net.hba(c, NETS).splitlines()
    first = lambda s: next(i for i, x in enumerate(lines) if x.startswith(s))  # noqa: E731
    # a connection that lost its source address (shows as a gateway) never reaches anyone
    assert first("host all all 10.231.1.1/32 reject") < first("hostssl all +hub_readers")
    assert "host hub,postgres,template0,template1 +hub_readers all reject" in lines
    assert "hostssl all +hub_readers 10.0.0.0/8 scram-sha-256" in lines
    # readers from anywhere else are refused before the service rules; services only from the Docker networks
    assert first("host all +hub_readers all reject") < first("host all all 10.231.1.0/24 scram-sha-256")
    assert lines[-1] == "host all all all reject" and not any(x.startswith("hostssl all all") for x in lines)


def test_no_allowed_range_means_no_reader_gets_in() -> None:
    lines = net.hba(cfg(HUB_DB_PUBLIC="1"), NETS).splitlines()
    assert not any(x.startswith("hostssl all +hub_readers") for x in lines)


def test_self_signed_certificate_and_fingerprint() -> None:
    cert, key = net.self_signed("hub.empresa.com")
    assert b"BEGIN CERTIFICATE" in cert and b"PRIVATE KEY" in key
    fp = net.fingerprint(cert)
    assert len(fp.split(":")) == 32 and fp == net.fingerprint(cert)
    assert b"BEGIN CERTIFICATE" in net.self_signed("10.1.2.3")[0]


def test_names_and_databases_never_reach_the_sql_unchecked() -> None:
    with pytest.raises(ValueError, match="nome"):
        q.role_of('x"; DROP ROLE hub; --')
    for bad in (("hub",), ("postgres",), ("template1",), ()):
        with pytest.raises(ValueError, match="bancos"):
            q.check_dbs(bad)
    sql = " ".join(
        q.create_sql(q.NewAccount("bi", "u1", ("inventory",), 30, "Ana"), "p'w", "2027-01-01", "x")
    )
    assert (
        "PASSWORD 'p''w'" in sql
        and "default_transaction_read_only = on" in sql
        and "GRANT hub_readers" in sql
    )


# ---- against a real Postgres --------------------------------------------------------------------------------


def url_for(db: str, user: str | None = None, pw: str | None = None) -> str:
    assert DB_URL
    scheme, rest = DB_URL.split("://", 1)
    cred, host = rest.split("@", 1)
    if user:
        cred = f"{user}:{pw}"
    return f"{scheme}://{cred}@{host.rsplit('/', 1)[0]}/{db}"


async def run_pg(db: str, stmts: list[str]) -> str:
    """The psql of the database container, on the test server: statements in order, the last result as text."""
    c = await asyncpg.connect(url_for(db))
    try:
        out = ""
        for s in stmts:
            out = (
                str(await c.fetchval(s)) if s.lstrip().upper().startswith("SELECT") else (await c.execute(s))
            )
        return out
    finally:
        await c.close()


@pytest.fixture
async def app_dbs() -> AsyncIterator[tuple[str, str]]:
    app, other = f"t_{secrets.token_hex(4)}", f"t_{secrets.token_hex(4)}"
    owner = f"o_{secrets.token_hex(4)}"
    su = await asyncpg.connect(url_for("postgres"))
    try:
        await su.execute(f"CREATE ROLE \"{owner}\" LOGIN PASSWORD 'x'")
        for d in (app, other):
            await su.execute(f'CREATE DATABASE "{d}" OWNER "{owner}"')
            await su.execute(f'REVOKE ALL ON DATABASE "{d}" FROM PUBLIC')
        c = await asyncpg.connect(url_for(app))
        await c.execute(f'SET ROLE "{owner}"; CREATE TABLE itens (id int); INSERT INTO itens VALUES (1)')  # noqa: S608
        await c.close()
        yield app, other
    finally:
        for r in await su.fetch("SELECT rolname FROM pg_roles WHERE starts_with(rolname, 'ro_t')"):
            await su.execute(f'DROP ROLE "{r[0]}"')
        for d in (app, other):
            await su.execute(f'DROP DATABASE IF EXISTS "{d}" WITH (FORCE)')
            await su.execute(f'DROP ROLE IF EXISTS "hub_read_{d}"')
        await su.execute(f'DROP ROLE IF EXISTS "{owner}"')
        await su.close()


@pg
async def test_a_read_account_reads_its_databases_and_nothing_more(app_dbs: tuple[str, str]) -> None:
    app, other = app_dbs
    acc = DbAccess(run_pg)
    assert app in await acc.databases() and "hub" not in await acc.databases()
    name = f"t{secrets.token_hex(3)}"
    role, pw = await acc.create(q.NewAccount(name, "u-ana", (app,), 30, "Admin"))
    c = await asyncpg.connect(url_for(app, role, pw))
    try:
        assert await c.fetchval("SELECT count(*) FROM itens") == 1
        with pytest.raises(asyncpg.ReadOnlySQLTransactionError):
            await c.execute("INSERT INTO itens VALUES (2)")
        assert await c.fetchval("SHOW statement_timeout") == "1min"
    finally:
        await c.close()
    # a table the app creates later is readable too (default privileges of the database's owner)
    owner_conn = await asyncpg.connect(url_for(app))
    owner = await owner_conn.fetchval(
        "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = current_database()"
    )
    await owner_conn.execute(f'SET ROLE "{owner}"; CREATE TABLE novos (id int)')
    await owner_conn.close()
    c = await asyncpg.connect(url_for(app, role, pw))
    assert await c.fetchval("SELECT count(*) FROM novos") == 0
    await c.close()
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await asyncpg.connect(url_for(other, role, pw))
    listed = next(a for a in await acc.accounts() if a["role"] == role)
    assert listed["state"] == "ativa" and listed["owner"] == "u-ana" and listed["dbs"] == [app]


@pg
async def test_new_password_suspension_expiry_and_removal(app_dbs: tuple[str, str]) -> None:
    app, _ = app_dbs
    acc = DbAccess(run_pg)
    role, pw = await acc.create(q.NewAccount(f"t{secrets.token_hex(3)}", "u-bia", (app,), 30, "Admin"))
    pw2 = await acc.new_password(role)
    with pytest.raises(asyncpg.InvalidPasswordError):
        await asyncpg.connect(url_for(app, role, pw))
    (await asyncpg.connect(url_for(app, role, pw2))).terminate()
    assert await acc.suspend_owner("u-bia") == [role]
    with pytest.raises(asyncpg.InvalidAuthorizationSpecificationError):
        await asyncpg.connect(url_for(app, role, pw2))
    assert next(a for a in await acc.accounts() if a["role"] == role)["state"] == "suspensa"
    later = datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=31)
    assert next(a for a in await acc.accounts(later) if a["role"] == role)["state"] == "expirada"
    await acc.remove(role)
    assert all(a["role"] != role for a in await acc.accounts())
    with pytest.raises(LookupError):
        await acc.remove(role)


# ---- Administration page and API ---------------------------------------------------------------------------


@pg
async def test_admin_creates_rotates_suspends_and_removes(
    app_dbs: tuple[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _ = app_dbs
    hub = make_hub(tmp_path, monkeypatch)
    hub.app.state.dbaccess = DbAccess(run_pg)  # type: ignore[attr-defined]
    page = hub.get("/admin/users?tab=db").text
    assert "Acesso ao banco" in page and 'data-dbaccess-status="off"' in page and app in page
    assert 'href="/admin/users?tab=db"' in hub.get("/admin/users").text
    add_user(hub, "bia", ["User"])
    name = f"t{secrets.token_hex(3)}"
    r = hub.post("/api/admin/db-access", json={"name": name, "owner": "bia", "dbs": [app], "days": 30})
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["role"] == f"ro_{name}" and len(out["password"]) >= 24 and out["connection"]["port"] == 5432
    assert out["password"] not in json.dumps(hub.get("/api/admin/db-access").json())
    row = hub.get("/admin/users?tab=db").text.split(f'data-dbaccess-row="ro_{name}"')[1].split("</tr>")[0]
    assert "Bia" in row and "Ativa" in row
    assert hub.post(f"/api/admin/db-access/ro_{name}/password", json={}).json()["password"] != out["password"]
    assert (
        hub.post("/api/admin/db-access", json={"name": "x2", "owner": "bia", "dbs": ["hub"]}).status_code
        == 400
    )
    leitor = add_user(hub, "leitor", ["User"])
    assert leitor.get("/api/admin/db-access").status_code == 403
    assert leitor.get("/admin/users?tab=db").status_code == 403
    # the responsible person is deactivated: the account stops (and cannot get a new password while so)
    assert hub.put("/api/admin/users/bia", json={"active": False}).status_code == 200
    states = {a["role"]: a["state"] for a in hub.get("/api/admin/db-access").json()["accounts"]}
    assert states[f"ro_{name}"] == "suspensa"
    assert hub.post(f"/api/admin/db-access/ro_{name}/password", json={}).status_code == 400
    assert (
        hub.delete(
            f"/api/admin/db-access/ro_{name}", headers={"content-type": "application/json"}
        ).status_code
        == 204
    )
    titles = [e["title"] for e in hub.get("/api/v1/audit?limit=20").json()["items"]]
    assert "Conta de leitura do banco criada" in titles and "Contas de leitura do banco suspensas" in titles


# ---- applying the rules inside the database container -----------------------------------------------------


class FakeDbContainer:
    """The Postgres container as `exec_in`/`_psql` see it: files by path, scripts run, SQL received."""

    def __init__(self) -> None:
        self.files = {"hba": "host all all all scram-sha-256\n", "crt": ""}
        self.scripts: list[str] = []
        self.sql: list[str] = []
        self.engine = self
        self.db_container = "app-hub-app-db"

    async def _run(self, fn: object) -> dict[str, object]:
        return {"NetworkSettings": {"Networks": {"default": {"IPAddress": "172.20.0.3", "IPPrefixLen": 16, "Gateway": "172.20.0.1"}}}}  # fmt: skip

    async def exec_in(self, _e: object, _c: str, cmd: list[str], files: dict[str, bytes] | None = None, user: str = "") -> tuple[int, bytes]:  # fmt: skip
        script = cmd[-1]
        self.scripts.append(script)
        for path, data in (files or {}).items():
            key = "hba" if "pg_hba" in script and "hba" in path else ("crt" if "dbcrt" in path else "key")
            self.files[key] = data.decode()
        if script.startswith("cat") and "pg_hba" in script:
            return 0, self.files["hba"].encode()
        if script.startswith("cat"):
            return 0, self.files["crt"].encode()
        return 0, b""

    async def _psql(self, db: str, *stmts: str) -> str:
        self.sql += stmts
        return "0\n" if "pg_hba_file_rules" in stmts[-1] else ""


async def test_sync_leaves_an_untouched_install_alone_and_applies_when_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hub.admin import dbaccess_server as srv

    box = FakeDbContainer()
    monkeypatch.setattr(srv, "exec_in", box.exec_in)
    assert (await srv.sync(box, cfg()))["public"] is False and box.sql == []  # never turned on: not touched
    on = cfg(HUB_DB_PUBLIC="1", HUB_DB_ALLOWED_CIDRS="10.0.0.0/8")
    state = await srv.sync(box, on)
    assert state["error"] == "" and len(state["fingerprint"].split(":")) == 32
    assert (
        "hostssl all +hub_readers 10.0.0.0/8" in box.files["hba"]
        and "172.20.0.1/32 reject" in box.files["hba"]
    )
    assert "ALTER SYSTEM SET ssl = 'on'" in box.sql and "BEGIN CERTIFICATE" in box.files["crt"]
    fp, writes = state["fingerprint"], sum("install" in s for s in box.scripts)
    again = await srv.sync(box, on)  # same certificate, same rules: nothing rewritten
    assert again["fingerprint"] == fp and sum("install" in s for s in box.scripts) == writes
    off = await srv.sync(box, cfg())  # turned off after being on: back to the default rule
    assert off["public"] is False and box.files["hba"].rstrip().endswith("host all all all scram-sha-256")
