"""Database side of backups: admin `psql` runs in the Postgres container, the scratch test restore, the
non-transactional restore that keeps the current database "aside", and the aside databases left behind."""

# ruff: noqa: S608 - admin SQL built with escaped literals/identifiers (`_lit`/`_qi`)
from __future__ import annotations

import contextlib
import re
import time
from typing import Any

from hub.ops.model import OpsError, human, now_ms
from hub.ops.runtime import Engine, exec_in

RESTORE_FILE = "/tmp/hub-restore.sql"  # noqa: S108
ADMIN_FILE = "/tmp/hub-admin.sql"  # noqa: S108
SYSTEM_DBS = {"postgres", "template0", "template1"}
_DB = re.compile(r"^[A-Za-z0-9_]{1,63}$")
_BUCKET = re.compile(r"^[a-z0-9][a-z0-9.-]{2,62}$")


def _lit(v: str) -> str:
    return "'" + v.replace("'", "''") + "'"


def _qi(v: str) -> str:
    return '"' + v.replace('"', '""') + '"'


def _errtail(out: bytes) -> str:
    """psql error lines (the end of the output is usually just setval noise)."""
    text = out.decode(errors="replace")
    errs = [ln.strip() for ln in text.splitlines() if "ERROR" in ln or "FATAL" in ln]
    return (" | ".join(errs[:3]) or text[-200:])[-300:]


def chown_sql(owner: str) -> str:
    """Hands ownership of the tables, sequences and views in the public schema to the owner (the clone dump comes without owners)."""
    o = _lit(owner)
    return (
        "DO $$DECLARE r record; BEGIN FOR r IN "
        "SELECT 'TABLE' k, tablename n FROM pg_tables WHERE schemaname = 'public' "
        "UNION ALL SELECT 'SEQUENCE', sequencename FROM pg_sequences WHERE schemaname = 'public' "
        "UNION ALL SELECT 'VIEW', viewname FROM pg_views WHERE schemaname = 'public' "
        "UNION ALL SELECT 'MATERIALIZED VIEW', matviewname FROM pg_matviews WHERE schemaname = 'public' "
        f"LOOP EXECUTE format('ALTER %s %I OWNER TO %I', r.k, r.n, {o}); END LOOP; END$$"
    )


def redact(text: str, *secrets_: str) -> str:
    for s in secrets_:
        if s:
            text = text.replace(s, "••••")
    return re.sub(r"(PASSWORD\s+')[^']*'", r"\1••••'", text)[:300]


class BackupDb:
    """Base of `Backups`: what talks to the Postgres container (`engine`, `db_container`, `db_admin`)."""

    engine: Engine
    db_container: str
    db_admin: str

    async def _psql(self, db: str, *stmts: str) -> str:
        """Administration commands (each in autocommit, as `DROP DATABASE` requires); returns the output."""
        sql = ";\n".join(stmts) + ";\n"
        rc, out = await exec_in(
            self.engine, self.db_container,
            ["psql", "-U", self.db_admin, "-d", db, "-v", "ON_ERROR_STOP=1", "-q", "-t", "-A", "-f", ADMIN_FILE],
            files={ADMIN_FILE: sql.encode()},
        )  # fmt: skip
        text = out.decode(errors="replace")
        if rc != 0:
            raise OpsError(text.strip()[-200:] or "psql falhou")
        return text

    async def _load(self, db: str, sql: bytes, single: bool) -> tuple[int, bytes]:
        cmd = [
            "psql",
            "-U",
            self.db_admin,
            "-d",
            db,
            "-v",
            "ON_ERROR_STOP=1",
            *(["-1"] if single else []),
            "-q",
            "-f",
            RESTORE_FILE,
        ]
        return await exec_in(self.engine, self.db_container, cmd, files={RESTORE_FILE: sql})

    async def _restore_db(self, db: str, sql: bytes, scratch: bool) -> str:
        if not scratch:
            return await self._restore_db_aside(db, sql)
        target = f"{db}_rt{int(time.time())}"
        rc, out = await exec_in(self.engine, self.db_container, ["createdb", "-U", self.db_admin, target])
        if rc != 0:
            raise OpsError(f"createdb {target}: {out.decode(errors='replace')[:200]}")
        try:
            rc, out = await self._load(target, sql, single=True)
            if rc != 0:
                raise OpsError(f"restauração de {db} falhou (nada foi alterado): {_errtail(out)}")
        finally:
            await exec_in(
                self.engine, self.db_container, ["dropdb", "-U", self.db_admin, "--if-exists", target]
            )
        return f"banco {db} verificado em banco temporário ({human(len(sql))})"

    async def _restore_db_aside(self, db: str, sql: bytes) -> str:  # noqa: C901, PLR0915
        """Non-transactional mode: the current database is renamed "aside" (`<db>_pre<n>`, intact), the dump goes into a new
        database with the original name, and only at the end is the aside copy deleted; any failure gives the original its name back."""
        if db in SYSTEM_DBS or db.startswith("pg_") or not _DB.match(db):
            raise OpsError(f"Banco recusado na restauração: {db}")
        aside = f"{db[:40]}_pre{now_ms() % 10_000_000_000}"
        d, a = _lit(db), _lit(aside)
        had: bool | None = None  # None until the rename answers: a failure still restores the original
        try:
            out = await self._psql(
                "postgres",
                f"DO $$DECLARE i int := 0; BEGIN IF EXISTS (SELECT 1 FROM pg_database WHERE datname = {d}) THEN LOOP "
                f"PERFORM pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = {d} AND pid <> pg_backend_pid(); "
                f"PERFORM pg_stat_clear_snapshot(); EXIT WHEN i > 100 OR NOT EXISTS (SELECT 1 FROM pg_stat_activity "
                f"WHERE datname = {d} AND pid <> pg_backend_pid()); PERFORM pg_sleep(0.1); i := i + 1; END LOOP; "
                f"EXECUTE format('ALTER DATABASE %I RENAME TO %I', {d}, {a}); END IF; END$$",
                f"SELECT 'hub:aside=' || EXISTS (SELECT 1 FROM pg_database WHERE datname = {a})",
                f"SELECT 'hub:owner=' || coalesce((SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = {a}), '')",
            )
            had = "hub:aside=true" in out
            owner = next(
                (ln[10:] for ln in out.splitlines() if ln.startswith("hub:owner=") and ln[10:]), self.db_admin
            )
            await self._psql(
                "postgres",
                f"CREATE DATABASE {_qi(db)} OWNER {_qi(owner)}",
                f"REVOKE ALL ON DATABASE {_qi(db)} FROM PUBLIC",
                f"GRANT CONNECT, TEMPORARY ON DATABASE {_qi(db)} TO {_qi(owner)}",
                f"DO $$DECLARE c text; BEGIN SELECT shobj_description(oid, 'pg_database') INTO c FROM pg_database "
                f"WHERE datname = {a}; IF c IS NOT NULL THEN EXECUTE format('COMMENT ON DATABASE %I IS %L', {d}, c); END IF; END$$",
            )
            rc, out2 = await self._load(db, sql, single=False)
            if rc != 0:
                raise OpsError(_errtail(out2))
            if (
                owner != self.db_admin
            ):  # dump without owners, loaded by the administrator: the objects go back to the app's role
                await self._psql(db, chown_sql(owner))
        except OpsError as e:
            undo = [f"DROP DATABASE IF EXISTS {_qi(db)} WITH (FORCE)"]
            if had:
                undo.append(f"ALTER DATABASE {_qi(aside)} RENAME TO {_qi(db)}")
            if had is not None:
                with contextlib.suppress(OpsError):
                    await self._psql("postgres", *undo)
            raise OpsError(
                f"restauração de {db} falhou ({'banco original devolvido' if had else 'sem banco anterior'}): {e.message}"
            ) from e
        if had:
            try:
                await self._psql("postgres", f"DROP DATABASE IF EXISTS {_qi(aside)} WITH (FORCE)")
            except OpsError:
                return f"banco {db} restaurado; o anterior ficou de lado como {aside} (veja Bancos de lado)"
        return f"banco {db} restaurado (de lado até o fim, {human(len(sql))})"

    async def list_aside(self, known: list[tuple[str, str, str]]) -> list[dict[str, Any]]:
        """`<db>_pre<n>` databases left behind by an interrupted restore; `known` = (app, env, database) of the apps."""
        out = await self._psql(
            "postgres", "SELECT 'hub:db=' || datname FROM pg_database WHERE NOT datistemplate"
        )
        names = [ln.strip()[7:] for ln in out.splitlines() if ln.strip().startswith("hub:db=")]
        res: list[dict[str, Any]] = []
        for app, env, db in known:
            if db in SYSTEM_DBS or any(r["db"] == db for r in res):
                continue
            pre = f"{db[:40]}_pre"
            res += [{"app": app, "env": env, "db": db, "aside": n, "db_exists": db in names}
                    for n in names if n.startswith(pre) and n[len(pre):].isdigit()]  # fmt: skip
        return res

    async def aside_action(
        self, known: list[tuple[str, str, str]], aside: str, action: str
    ) -> dict[str, Any]:
        a = next((x for x in await self.list_aside(known) if x["aside"] == aside), None)
        if a is None:
            raise OpsError("Banco de lado não encontrado (ou restauração em curso).", 404)
        if action == "rename-back":
            if a["db_exists"]:
                raise OpsError(
                    f"o banco {a['db']} já existe: confira os dados e apague o de lado, ou renomeie manualmente",
                    409,
                )
            await self._psql("postgres", f"ALTER DATABASE {_qi(aside)} RENAME TO {_qi(a['db'])}")
        else:
            await self._psql("postgres", f"DROP DATABASE IF EXISTS {_qi(aside)} WITH (FORCE)")
        return a
