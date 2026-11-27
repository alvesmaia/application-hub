"""Remove an application: its databases (`app_<id>`, `app_<id>_staging`) and the hub's roles around them.

Only objects that carry the hub's marker are dropped (`hub-managed:app=<id>[:env=staging]`, or `hub-managed:demo=<id>`
for a sample app); anything else is refused and left alone. The commands run in the Postgres container (`Backups._psql`)."""

# ruff: noqa: S608 - admin SQL with escaped literals/identifiers
from __future__ import annotations

from typing import Any

from hub.admin.dbaccess_sql import GROUP_PREFIX, ROLE_PREFIX
from hub.admin.demo_db import marker as demo_marker
from hub.ops import provision as pv
from hub.ops.backups import _lit, _qi, pg_backup_role

_RO_LIKE = ROLE_PREFIX.removesuffix("_") + "\\_%"  # LIKE pattern of the read accounts (`ro_*`)


def markers(app: str, env: str) -> set[str]:
    out = {pv.marker(app, env)}
    return out | {demo_marker(app)} if env == "producao" else out


def verdict(name: str, role_c: str | None, owner: str | None, db_c: str | None, allowed: set[str]) -> str:
    """`absent` (nothing there), `drop` (marked by the hub) or `refuse` (someone else's object)."""
    if role_c is None and owner is None:
        return "absent"
    if role_c is not None and role_c not in allowed:
        return "refuse"
    if owner is not None and (owner != name or (role_c is None and db_c not in allowed)):
        return "refuse"
    return "drop"


def forget_in_read_accounts(db: str) -> str:
    """Read accounts (`ro_*`, comment = JSON with `dbs`) stop listing the dropped database."""
    d = _lit(db)
    return (
        "DO $$DECLARE r record; BEGIN FOR r IN SELECT rolname, shobj_description(oid, 'pg_authid')::jsonb AS m "
        f"FROM pg_roles WHERE rolname LIKE '{_RO_LIKE}' "
        "AND shobj_description(oid, 'pg_authid') LIKE '{%' LOOP "
        f"IF r.m->'dbs' ? {d} THEN EXECUTE format('COMMENT ON ROLE %I IS %L', r.rolname, "
        f"jsonb_set(r.m, '{{dbs}}', (r.m->'dbs') - {d})::text); END IF; END LOOP; END$$"
    )


def drop_sql(name: str) -> list[str]:
    return [
        f"DROP DATABASE IF EXISTS {_qi(name)} WITH (FORCE)",
        f"DROP ROLE IF EXISTS {_qi(name)}",
        f"DROP ROLE IF EXISTS {_qi(GROUP_PREFIX + name)}",
        f"DROP ROLE IF EXISTS {_qi(pg_backup_role(name))}",
        forget_in_read_accounts(name),
    ]


async def drop(b: Any, app: str, env: str) -> str:
    """Drops one environment's database; returns the line for the job log (never raises on a refusal)."""
    name = pv.db_name(app, env)
    role_c, owner, db_c = await pv._state(b, name)
    v = verdict(name, role_c, owner, db_c, markers(app, env))
    if v == "absent":
        return f"banco {name}: não existe"
    if v == "refuse":
        return f"banco {name}: mantido (não foi criado pelo hub)"
    await b._psql("postgres", *drop_sql(name))
    return f"banco {name} apagado"
