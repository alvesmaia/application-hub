"""SQL of the read accounts (pure: lists of statements and the queries that read them back).

A read account is a LOGIN role `ro_<name>`, member of `hub_readers` (the only roles `pg_hba.conf` lets in from
outside) and of one `hub_read_<db>` group per database it may read. The group holds CONNECT, USAGE on the schemas,
SELECT on every table and the default privileges, so tables the app creates later are readable too. The account
itself is read-only (`default_transaction_read_only`), has a statement timeout and a connection limit, and expires
in Postgres (`VALID UNTIL`). Who is responsible, what it reads and who made it live in the role's comment (JSON):
nothing else to keep in sync."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from hub.admin.dbaccess_net import NEVER, READERS

ROLE_PREFIX, GROUP_PREFIX = "ro_", "hub_read_"
_NAME = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
_DB = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
LIMITS = ("SET default_transaction_read_only = on", "SET statement_timeout = '60s'",
          "SET idle_in_transaction_session_timeout = '60s'")  # fmt: skip


@dataclass(frozen=True)
class NewAccount:
    name: str  # without the prefix: "powerbi_vendas"
    owner: str  # responsible person (user id)
    dbs: tuple[str, ...]
    days: int
    by: str  # who made it (name, for the list)


def qi(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def lit(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def role_of(name: str) -> str:
    if not _NAME.match(name):
        raise ValueError(f"nome inválido: {name!r} (letras minúsculas, números e _, começando por letra)")
    return ROLE_PREFIX + name


def check_dbs(dbs: tuple[str, ...]) -> None:
    bad = [d for d in dbs if not _DB.match(d) or d in NEVER or d.startswith("template")]
    if bad or not dbs:
        raise ValueError(
            f"bancos inválidos: {bad or 'nenhum'} (escolha bancos existentes; nunca {', '.join(NEVER)})"
        )


def readers_sql() -> list[str]:
    return [f"DO $$BEGIN CREATE ROLE {READERS} NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END$$"]


def group_cluster_sql(db: str) -> list[str]:
    """Cluster level (any database): the group and its CONNECT."""
    g = GROUP_PREFIX + db
    return [f"DO $$BEGIN CREATE ROLE {qi(g)} NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END$$",
            f"GRANT CONNECT ON DATABASE {qi(db)} TO {qi(g)}"]  # fmt: skip


def group_db_sql(db: str) -> list[str]:
    """Inside `db`: read on what exists and, by default, on what the database's owner creates later."""
    g = qi(GROUP_PREFIX + db)
    return [
        f"""DO $$DECLARE s text; o text;
BEGIN
  SELECT pg_get_userbyid(datdba) INTO o FROM pg_database WHERE datname = current_database();
  FOR s IN SELECT nspname FROM pg_namespace WHERE nspname NOT LIKE 'pg\\_%' AND nspname <> 'information_schema' LOOP
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO {g}', s);
    EXECUTE format('GRANT SELECT ON ALL TABLES IN SCHEMA %I TO {g}', s);
  END LOOP;
  EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE %I GRANT USAGE ON SCHEMAS TO {g}', o);
  EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE %I GRANT SELECT ON TABLES TO {g}', o);
END$$"""  # noqa: S608 - names quoted by qi()
    ]


def create_sql(a: NewAccount, password: str, until: str, now: str) -> list[str]:
    r = qi(role_of(a.name))
    meta = json.dumps({"owner": a.owner, "dbs": list(a.dbs), "by": a.by, "at": now}, ensure_ascii=False)
    return [f"CREATE ROLE {r} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE CONNECTION LIMIT 5 "
            f"PASSWORD {lit(password)} VALID UNTIL {lit(until)}",
            f"GRANT {READERS} TO {r}", *(f"GRANT {qi(GROUP_PREFIX + d)} TO {r}" for d in a.dbs),
            *(f"ALTER ROLE {r} {s}" for s in LIMITS), f"COMMENT ON ROLE {r} IS {lit(meta)}"]  # fmt: skip


def password_sql(role: str, password: str) -> list[str]:
    return [f"ALTER ROLE {qi(role)} LOGIN PASSWORD {lit(password)}"]


def disable_sql(role: str) -> list[str]:
    """No new logins, and the open sessions end now."""
    return [f"ALTER ROLE {qi(role)} NOLOGIN",
            f"SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity WHERE usename = {lit(role)}"]  # noqa: S608  # fmt: skip


def drop_sql(role: str) -> list[str]:
    return [*disable_sql(role), f"DROP ROLE {qi(role)}"]


LIST_SQL = f"""SELECT coalesce(json_agg(json_build_object(
  'role', r.rolname, 'login', r.rolcanlogin, 'until', r.rolvaliduntil, 'meta', shobj_description(r.oid, 'pg_authid'))
  ORDER BY r.rolname), '[]')
FROM pg_roles r WHERE starts_with(r.rolname, '{ROLE_PREFIX}') AND pg_has_role(r.oid, '{READERS}', 'MEMBER')
  AND r.rolname <> '{READERS}'"""  # noqa: S608 - constants

DBS_SQL = """SELECT coalesce(json_agg(datname ORDER BY datname), '[]') FROM pg_database
WHERE NOT datistemplate AND datname <> 'postgres'"""
