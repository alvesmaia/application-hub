"""«Provision database» and «Rotate database password» (port of `hub-ops::provision`): a role and database of its own per app and
environment (`app_<id>`, `app_<id>_staging`), created only if marked by the hub (`COMMENT … hub-managed:app=<id>`); the connection
string becomes a managed variable (`DATABASE_URL_<APP>[_STAGING]` → `HUB_DB_URL`). The commands run in the Postgres container."""

# ruff: noqa: S608 - admin SQL with escaped literals/identifiers
from __future__ import annotations

import os
import re
import secrets
from typing import Any

from hub.admin.config import env_suffix
from hub.ops.backups import Backups, _lit, _qi
from hub.ops.model import OpsError
from hub.ops.runtime import exec_in

DB_PREFIX = "app_"
_ID = re.compile(r"^[a-z][a-z0-9_]{0,47}$")


def db_name(app: str, env: str) -> str:
    base = app.replace("-", "_")
    if not _ID.match(base):
        raise OpsError(f"id de app inválido para banco: {app}")
    return f"{DB_PREFIX}{base}" + ("_staging" if env == "staging" else "")


def marker(app: str, env: str) -> str:
    return f"hub-managed:app={app}" + (":env=staging" if env == "staging" else "")


def variable(app: str, env: str) -> str:
    return f"DATABASE_URL_{env_suffix(app)}" + ("_STAGING" if env == "staging" else "")


def host() -> str:
    return os.getenv("HUB_DB_HOST", "app-hub-app-db:5432")


async def _state(b: Backups, name: str) -> tuple[str | None, str | None, str | None]:
    """(role comment | None if it does not exist, database owner, database comment)."""
    n = _lit(name)
    out = await b._psql(
        "postgres",
        f"SELECT 'role=' || coalesce((SELECT coalesce(shobj_description(oid, 'pg_authid'), '') FROM pg_roles WHERE rolname = {n}), '!')",
        f"SELECT 'owner=' || coalesce((SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = {n}), '!')",
        f"SELECT 'dbc=' || coalesce((SELECT coalesce(shobj_description(oid, 'pg_database'), '') FROM pg_database WHERE datname = {n}), '')",
    )
    kv = dict(ln.split("=", 1) for ln in out.splitlines() if "=" in ln)
    role = kv.get("role", "!")
    owner = kv.get("owner", "!")
    return (None if role == "!" else role), (None if owner == "!" else owner), kv.get("dbc")


async def works(b: Backups, name: str, url: str) -> bool:
    pw = url.split("://", 1)[1].split("@", 1)[0].split(":", 1)[1]
    try:
        rc, _ = await exec_in(b.engine, b.db_container, ["psql", "-h", b.db_container, "-U", name, "-d", name, "-tAc", "SELECT 1"],
                              env={"PGPASSWORD": pw})  # fmt: skip
    except OpsError:
        return False
    return rc == 0


def _ddl(name: str, mark: str, pw: str, role_c: str | None, owner: str | None) -> list[str]:
    q = _qi(name)
    stmts = [f"ALTER ROLE {q} WITH LOGIN PASSWORD {_lit(pw)}" if role_c is not None else
             f"CREATE ROLE {q} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {_lit(pw)} CONNECTION LIMIT 40",
             f"COMMENT ON ROLE {q} IS {_lit(mark)}"]  # fmt: skip
    if owner is None:
        stmts += [f"CREATE DATABASE {q} OWNER {q}", f"COMMENT ON DATABASE {q} IS {_lit(mark)}"]
    stmts.append(f"REVOKE ALL ON DATABASE {q} FROM PUBLIC")
    stmts.append(f"GRANT CONNECT, TEMPORARY ON DATABASE {q} TO {q}")
    return stmts


async def _unbind_other_staging_dbs(cfg: Any, app: str, var: str) -> None:
    """Staging never stores a production database credential."""
    async with cfg.s.locked(True) as d:
        for v in list(d["env"]):
            mine = [x for x in v["bindings"] if x["app"] == app and x["env"] == "staging"]
            if v["name"] == var or v.get("inject_as") != "HUB_DB_URL" or not mine:
                continue
            v["bindings"] = [x for x in v["bindings"] if x not in mine]
            if not v["bindings"]:
                d["env"].remove(v)


async def provision(b: Backups, cfg: Any, actor: str, app: str, env: str) -> dict[str, Any]:
    name, mark, var = db_name(app, env), marker(app, env), variable(app, env)
    if name in ("postgres", b.db_admin) or name.startswith("pg_"):
        raise OpsError(f"nome reservado para banco/usuário: {name}")
    data = await cfg.read()
    cur = next((v for v in data["env"] if v["name"] == var), None)
    if cur is not None and not cur.get("managed"):
        raise OpsError(
            f"a variável {var} já existe e não é gerenciada pela plataforma; renomeie ou exclua-a antes"
        )
    if cur is not None and await works(b, name, str(cur["value"])):
        return {
            "variable": var,
            "database": name,
            "changed": False,
            "created_role": False,
            "created_database": False,
        }
    role_c, owner, db_c = await _state(b, name)
    if role_c is not None and role_c != mark:
        raise OpsError(f"o papel {name} já existe e não foi criado pelo hub: recusado")
    if owner is not None and (owner != name or db_c != mark):
        raise OpsError(f"o banco {name} já existe e não foi criado pelo hub: recusado")
    pw = secrets.token_hex(24)
    await b._psql("postgres", *_ddl(name, mark, pw, role_c, owner))
    bindings = [{"app": app, "env": env}]
    if env == "staging":
        await _unbind_other_staging_dbs(cfg, app, var)
    await cfg.upsert_managed(actor, {"name": var, "value": f"postgresql://{name}:{pw}@{host()}/{name}",
                                     "kind": "connection_string", "bindings": bindings, "inject_as": "HUB_DB_URL",
                                     "managed": True}, "Banco provisionado")  # fmt: skip
    return {"variable": var, "database": name, "changed": True, "created_role": role_c is None,
            "created_database": owner is None}  # fmt: skip


async def rotate(b: Backups, cfg: Any, actor: str, app: str, env: str = "producao") -> str:
    name, var = db_name(app, env), variable(app, env)
    if not any(v["name"] == var and v.get("managed") for v in (await cfg.read())["env"]):
        raise OpsError(
            "banco não provisionado pela plataforma: use Provisionar banco em Containers › detalhes do app",
            409,
        )
    role_c, _, _ = await _state(b, name)
    if role_c != marker(app, env):
        raise OpsError(
            f"o papel {name} não existe (ou não é do hub): use Provisionar banco em Containers › detalhes do app",
            409,
        )
    pw = secrets.token_hex(24)
    await b._psql("postgres", f"ALTER ROLE {_qi(name)} WITH PASSWORD {_lit(pw)}")
    await cfg.rotate_managed(
        actor, var, f"postgresql://{name}:{pw}@{host()}/{name}", "Senha do banco rotacionada"
    )
    return var
