"""Backups: read-only credentials (`hub_bkp_<db>`, `bkp-<bucket>` in the vault), zip package (database + bucket + volumes) and restore.

Port of `hub-ops::creds` and the essentials of `dr`: `pg_dump`/`psql` run in the Postgres container (`docker exec`),
the bucket via `mc mirror`. Database restore in non-transactional mode (the current one goes "aside" and comes back if it fails),
optional off-site copy (`HUB_BACKUP_OFFSITE_URL`) and the list of side databases; the scheduler is in `scheduler.py`."""

# ruff: noqa: S608 - admin SQL built with escaped literals/identifiers (`_lit`/`_qi`)
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import json
import os
import re
import tempfile
import time
import zipfile
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from hub.connections.provision import ObjectStore
from hub.ops.model import OpsError, OpsStore, human, now_ms
from hub.ops.runtime import Engine, exec_in
from hub.storage import Body, StorageError

ROLE_FILE = "/tmp/hub-role.sql"  # noqa: S108 - inside the Postgres container
RESTORE_FILE = "/tmp/hub-restore.sql"  # noqa: S108
ADMIN_FILE = "/tmp/hub-admin.sql"  # noqa: S108
PG_ROLE_PREFIX, S3_BACKUP_PREFIX = "hub_bkp_", "bkp-"
SYSTEM_DBS = {"postgres", "template0", "template1"}
_DB = re.compile(r"^[A-Za-z0-9_]{1,63}$")
_BUCKET = re.compile(r"^[a-z0-9][a-z0-9.-]{2,62}$")
Progress = Callable[[int, str], None]


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


def pg_backup_role(db: str) -> str:
    return (PG_ROLE_PREFIX + db.replace("-", "_"))[:63]


def pg_backup_marker(db: str) -> str:
    return f"hub-managed:backup={db}"


def s3_backup_user(bucket: str) -> str:
    return S3_BACKUP_PREFIX + bucket


def bucket_policy(bucket: str, write: bool = False) -> str:
    acts = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"] if write else ["s3:GetObject"]
    return json.dumps(
        {"Version": "2012-10-17", "Statement": [
            {"Effect": "Allow", "Action": ["s3:ListBucket", "s3:GetBucketLocation"], "Resource": [f"arn:aws:s3:::{bucket}"]},
            {"Effect": "Allow", "Action": acts, "Resource": [f"arn:aws:s3:::{bucket}/*"]}]}
    )  # fmt: skip


def backup_role_sql(db: str, pw: str) -> str:
    """`DO` block (run on the database by administration): read-only role, idempotent; a foreign role aborts (`HUB-ALIEN-ROLE`)."""
    if db in SYSTEM_DBS or not _DB.match(db):
        raise OpsError(f"Banco recusado para papel de backup: {db}")
    if len(pw) < 16 or not pw.isalnum():
        raise OpsError("senha do papel de backup inválida")
    r, m = pg_backup_role(db), pg_backup_marker(db)
    return f"""DO $hub$
DECLARE r text := '{r}'; m text := '{m}'; pw text := '{pw}'; priv boolean; cmt text; s record; o record;
BEGIN
  IF current_database() <> '{db}' THEN RAISE EXCEPTION 'HUB-WRONG-DB'; END IF;
  SELECT rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls, shobj_description(oid, 'pg_authid')
    INTO priv, cmt FROM pg_roles WHERE rolname = r;
  IF FOUND THEN
    IF priv OR cmt IS DISTINCT FROM m THEN RAISE EXCEPTION 'HUB-ALIEN-ROLE'; END IF;
    EXECUTE format('ALTER ROLE %I WITH LOGIN NOINHERIT PASSWORD %L', r, pw);
  ELSE
    EXECUTE format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT CONNECTION LIMIT 4 PASSWORD %L', r, pw);
    EXECUTE format('COMMENT ON ROLE %I IS %L', r, m);
  END IF;
  EXECUTE format('ALTER ROLE %I SET default_transaction_read_only = on', r);
  EXECUTE format('ALTER ROLE %I SET search_path = pg_catalog', r);
  EXECUTE format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), r);
  EXECUTE format('REVOKE TEMPORARY, CREATE ON DATABASE %I FROM %I', current_database(), r);
  FOR s IN SELECT nspname FROM pg_namespace WHERE nspname NOT LIKE 'pg\\_%' AND nspname <> 'information_schema' LOOP
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I', s.nspname, r);
    EXECUTE format('GRANT SELECT ON ALL TABLES IN SCHEMA %I TO %I', s.nspname, r);
    EXECUTE format('GRANT SELECT ON ALL SEQUENCES IN SCHEMA %I TO %I', s.nspname, r);
    FOR o IN SELECT DISTINCT pg_get_userbyid(cl.relowner) AS owner FROM pg_class cl
             JOIN pg_namespace n ON n.oid = cl.relnamespace WHERE n.nspname = s.nspname LOOP
      CONTINUE WHEN o.owner = r;
      EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I GRANT SELECT ON TABLES TO %I', o.owner, s.nspname, r);
      EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I GRANT SELECT ON SEQUENCES TO %I', o.owner, s.nspname, r);
    END LOOP;
  END LOOP;
END
$hub$;
"""


def redact(text: str, *secrets_: str) -> str:
    for s in secrets_:
        if s:
            text = text.replace(s, "••••")
    return re.sub(r"(PASSWORD\s+')[^']*'", r"\1••••'", text)[:300]


def _collect(tmp: str) -> dict[str, bytes]:
    return {
        f"bucket/{p.relative_to(tmp).as_posix()}": p.read_bytes() for p in Path(tmp).rglob("*") if p.is_file()
    }


def parse_targets(env_vars: dict[str, str]) -> tuple[str | None, str | None]:
    """(database, bucket) of the app from the injected variables (`HUB_DB_URL`, `HUB_S3_BUCKET`)."""
    url = env_vars.get("HUB_DB_URL", "")
    db = urlsplit(url).path.lstrip("/") if url else ""
    return (db or None), (env_vars.get("HUB_S3_BUCKET") or None)


class Backups:
    def __init__(
        self, store: OpsStore, engine: Engine, objstore: ObjectStore, data_dir: Path,
        app_env: Callable[[str, str], Awaitable[dict[str, str]]],
        hook: Callable[[str, str], Awaitable[None]] | None = None,
    ) -> None:  # fmt: skip
        self.store, self.engine, self.objstore, self.data_dir = store, engine, objstore, data_dir
        self.app_env, self.hook = app_env, hook
        self.db_container = os.getenv("HUB_BACKUP_DB_CONTAINER", "app-hub-app-db")
        self.db_admin = os.getenv("HUB_DB_ADMIN_USER", "hub")
        self.root = data_dir / "backups"
        self.conns: Any = None  # ConnStore (set by the app): `sharepoint://` off-site target

    # ---- credenciais ------------------------------------------------------------------------------------
    async def ensure_pg_role(self, db: str, rotate: bool = False) -> tuple[str, str]:
        pw = self.store.secret(f"backup.pg:{db}", rotate)
        sql = backup_role_sql(db, pw)
        rc, out = await exec_in(
            self.engine, self.db_container,
            ["psql", "-U", self.db_admin, "-d", db, "-v", "ON_ERROR_STOP=1", "-q", "-f", ROLE_FILE],
            files={ROLE_FILE: sql.encode()},
        )  # fmt: skip
        text = out.decode(errors="replace")
        if rc != 0:
            if "HUB-ALIEN-ROLE" in text:
                raise OpsError(
                    f"o papel {pg_backup_role(db)} já existe e não é o papel de backup do hub: recusado"
                )
            raise OpsError(f"papel de backup de {db}: {redact(text, pw)}", 502)
        return pg_backup_role(db), pw

    async def ensure_s3_user(self, bucket: str, rotate: bool = False) -> tuple[str, str]:
        if why := self.objstore.available():
            raise OpsError(why, 503)
        if not _BUCKET.match(bucket):
            raise OpsError(f"nome de bucket recusado: {bucket}")
        user, pw = s3_backup_user(bucket), self.store.secret(f"backup.s3:{bucket}", rotate)
        with tempfile.TemporaryDirectory(prefix="hub-pol-") as d:
            f = Path(d) / "policy.json"
            f.write_text(bucket_policy(bucket), encoding="utf-8")
            for step in (
                ("admin", "policy", "create", "hub", f"hub-{user}", str(f)),
                ("admin", "user", "add", "hub", user, pw),
                ("admin", "policy", "attach", "hub", f"hub-{user}", "--user", user),
            ):
                rc, out = await self.objstore.mc(*step)
                if rc != 0 and "already" not in out.lower():
                    raise OpsError(f"Object store: {redact(out, pw)}", 502)
        return user, pw

    async def rotate_all(self, apps: list[str]) -> list[str]:
        lines: list[str] = []
        for app in apps:
            for env in ("producao", "staging"):
                db, bucket = parse_targets(await self.app_env(app, env))
                for what, fn, tgt in (
                    ("banco", self.ensure_pg_role, db),
                    ("bucket", self.ensure_s3_user, bucket),
                ):
                    if not tgt:
                        continue
                    try:
                        user, _ = await fn(tgt, True)
                        lines.append(f"{app} ({env}) · {what} {tgt}: {user} rotacionada")
                    except OpsError as e:
                        lines.append(f"{app} ({env}) · {what} {tgt}: falhou — {e.message}")
        return lines

    # ---- pacote ---------------------------------------------------------------------------------------
    def _volume_dir(self, app: str, vol: str) -> Path:
        return self.data_dir / "apps" / app / vol

    async def create_bundle(
        self, app: str, env: str, volumes: list[str], hooks: bool, progress: Progress
    ) -> tuple[str, str, dict[str, Any]]:  # fmt: skip
        """Writes `<data>/backups/<app>/<app>-<ms>.zip`; returns (name, sha256, manifest)."""
        started = now_ms()
        db, bucket = parse_targets(await self.app_env(app, env))
        parts: list[dict[str, Any]] = []
        blobs: dict[str, bytes] = {}
        notes: list[str] = []
        paused = False
        if hooks and self.hook:
            progress(10, "Pausando gravações do app (hook pre)")
            await self.hook(app, "pre")
            paused = True
        try:
            if db:
                progress(25, f"Credencial só leitura do banco {db}")
                user, pw = await self.ensure_pg_role(db)
                progress(40, f"Copiando o banco {db} (pg_dump)")
                rc, out = await exec_in(
                    self.engine, self.db_container,
                    ["pg_dump", "-h", "127.0.0.1", "-U", user, "-d", db, "--no-owner", "--clean", "--if-exists"],
                    env={"PGPASSWORD": pw},
                )  # fmt: skip
                if rc != 0:
                    raise OpsError(f"pg_dump de {db} falhou: {redact(out.decode(errors='replace'), pw)}")
                blobs[f"db/{db}.sql"] = out
                parts.append({"kind": "db", "name": db})
            else:
                notes.append("app sem banco (HUB_DB_URL)")
            if bucket:
                progress(60, f"Copiando o bucket {bucket} (mc mirror)")
                blobs.update(await self._mirror_bucket(bucket))
                parts.append({"kind": "bucket", "name": bucket})
            for vol in volumes:
                base = self._volume_dir(app, vol)
                if base.is_dir():
                    for f in sorted(p for p in base.rglob("*") if p.is_file()):
                        blobs[f"files/{vol}/{f.relative_to(base).as_posix()}"] = f.read_bytes()
                    parts.append({"kind": "files", "name": vol})
        finally:
            if paused and self.hook:
                await self.hook(app, "post")
        if not parts:
            raise OpsError("Nada a copiar: o app não tem banco, bucket nem volumes com dados.")
        progress(80, "Empacotando e calculando sha256")
        man = {
            "version": 1, "app": app, "env": env, "at": started, "parts": parts, "notes": notes,
            "consistency": "consistente (hooks)" if hooks and self.hook else "crash-consistent",
            "sha256": {n: hashlib.sha256(b).hexdigest() for n, b in blobs.items()},
        }  # fmt: skip
        man["size"] = sum(len(b) for b in blobs.values())
        man["count"] = len(blobs)
        d = self.root / (app if env != "staging" else f"{app}{'__'}staging")
        d.mkdir(parents=True, exist_ok=True)
        name = f"{app}-{started}.zip"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("manifest.json", json.dumps(man, ensure_ascii=False))
            for n, b in blobs.items():
                z.writestr(n, b)
        (d / name).write_bytes(buf.getvalue())
        man["summary"] = " · ".join(
            f"{'banco' if p['kind'] == 'db' else 'bucket' if p['kind'] == 'bucket' else 'volume'} {p['name']}"
            for p in parts
        )
        return name, hashlib.sha256(buf.getvalue()).hexdigest(), man

    async def _mirror_bucket(self, bucket: str) -> dict[str, bytes]:
        user, pw = await self.ensure_s3_user(bucket)
        u = urlsplit(self.objstore.url)
        src = f"{u.scheme}://{quote(user)}:{quote(pw)}@{u.hostname}{f':{u.port}' if u.port else ''}"
        with (
            tempfile.TemporaryDirectory(prefix="hub-bk-") as tmp,
            tempfile.TemporaryDirectory(prefix="hub-mc-") as cfg,
        ):
            rc, out = await self.objstore.runner(
                ["--config-dir", cfg, "--quiet", "mirror", "--overwrite", f"bk/{bucket}", tmp],
                {"MC_HOST_bk": src},
            )
            if rc != 0:
                raise OpsError(f"mc mirror de {bucket} falhou: {redact(out, pw)}")
            return await asyncio.to_thread(_collect, tmp)

    # ---- restore ---------------------------------------------------------------------------------------
    def read_bundle(self, app: str, env: str, name: str, sha: str) -> tuple[dict[str, Any], dict[str, bytes]]:
        path = self.root / (app if env != "staging" else f"{app}__staging") / Path(name).name
        if not path.is_file():
            raise OpsError("Arquivo do backup não encontrado no disco.")
        raw = path.read_bytes()
        if sha and hashlib.sha256(raw).hexdigest() != sha:
            raise OpsError("sha256 do arquivo de backup não confere: arquivo alterado ou corrompido.")
        z = zipfile.ZipFile(io.BytesIO(raw))
        files = {n: z.read(n) for n in z.namelist() if n != "manifest.json"}
        man = json.loads(z.read("manifest.json"))
        for n, h in man["sha256"].items():
            if n not in files or hashlib.sha256(files[n]).hexdigest() != h:
                raise OpsError(f"Conferência sha256 falhou em {n}.")
        return man, files

    async def restore_bundle(
        self, app: str, env: str, name: str, sha: str, action: str, progress: Progress
    ) -> tuple[int, str]:
        if action == "clone":
            return await self.clone_to_staging(app, env, name, sha, progress)
        progress(20, "Conferindo sha256 de cada parte")
        man, files = self.read_bundle(app, env, name, sha)
        done: list[str] = []
        for n, data in files.items():
            if n.startswith("db/"):
                db = n[3:-4]
                progress(50, f"{'Testando' if action == 'test' else 'Restaurando'} o banco {db}")
                done.append(await self._restore_db(db, data, scratch=action == "test"))
        if action == "restore":
            for n, data in files.items():
                if n.startswith("files/"):
                    vol, rel = n[6:].split("/", 1)
                    dest = (self._volume_dir(app, vol) / rel).resolve()
                    if not str(dest).startswith(str(self._volume_dir(app, vol).resolve())):
                        raise OpsError(f"caminho inseguro no backup: {n}")
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(data)
            if any(n.startswith("bucket/") for n in files):
                done.append(
                    await self._restore_bucket(
                        next(p["name"] for p in man["parts"] if p["kind"] == "bucket"), files
                    )
                )
        return len(files), " · ".join(done) or f"{len(files)} arquivos conferidos (sha256)"

    async def clone_to_staging(
        self, app: str, env: str, name: str, sha: str, progress: Progress
    ) -> tuple[int, str]:
        """Clones the backup to staging: the dump goes into staging's own database (objects owned by its role) and the volumes
        into the `<app>__staging` folder; the production database/bucket are not touched."""
        progress(20, "Conferindo sha256 de cada parte")
        _, files = self.read_bundle(app, env, name, sha)
        sdb, _b = parse_targets(await self.app_env(app, "staging"))
        if not sdb:
            raise OpsError("Staging sem banco próprio: provisione o banco de staging (Variáveis › Banco próprio de staging) antes de clonar.")  # fmt: skip
        if sdb in {parse_targets(await self.app_env(app, "producao"))[0], *SYSTEM_DBS}:
            raise OpsError("O banco de staging não pode ser o de produção.")
        owner = (
            await self._psql(
                "postgres", f"SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = {_lit(sdb)}"
            )
        ).strip()
        if not owner:
            raise OpsError(f"O banco de staging {sdb} não existe: provisione-o antes de clonar.")
        done: list[str] = []
        for n, data in files.items():
            if n.startswith("db/"):
                progress(55, f"Clonando {n[3:-4]} para o banco de staging {sdb}")
                done.append(await self._restore_db_aside(sdb, data))
        stg = self.data_dir / "apps" / f"{app}__staging"
        for n, data in files.items():
            if n.startswith("files/"):
                vol, rel = n[6:].split("/", 1)
                dest = (stg / vol / rel).resolve()
                if not str(dest).startswith(str((stg / vol).resolve())):
                    raise OpsError(f"caminho inseguro no backup: {n}")
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
        return len(files), " · ".join(done) or f"{len(files)} arquivos clonados para staging"

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

    async def _restore_db_aside(self, db: str, sql: bytes) -> str:
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

    # ---- cópia externa -------------------------------------------------------------------------------------
    async def offsite(self, app: str, env: str, name: str) -> str | None:
        """Copies the package to `HUB_BACKUP_OFFSITE_URL` (`http(s)://key:secret@host:port/bucket[/prefix]`) and reads it back
        checking the sha256. `None` = not configured; a failure raises `OpsError` (the local backup still counts)."""
        url = os.getenv("HUB_BACKUP_OFFSITE_URL", "").strip()
        if not url:
            return None
        if url.startswith("sharepoint://"):
            return await self._offsite_sharepoint(url, app, env, name)
        u = urlsplit(url)
        parts = [p for p in u.path.split("/") if p]
        if not (
            u.scheme in ("http", "https") and u.hostname and u.username and parts and _BUCKET.match(parts[0])
        ):
            raise OpsError("HUB_BACKUP_OFFSITE_URL inválida")
        sub = app if env != "staging" else f"{app}__staging"
        key, src = "/".join([*parts, sub, name]), self.root / sub / name
        port = f":{u.port}" if u.port else ""
        host = f"{u.scheme}://{quote(u.username)}:{quote(u.password or '')}@{u.hostname}{port}"
        with (
            tempfile.TemporaryDirectory(prefix="hub-off-") as cfg,
            tempfile.TemporaryDirectory(prefix="hub-offr-") as back,
        ):
            for args in (["cp", str(src), f"off/{key}"], ["cp", f"off/{key}", back + "/x.zip"]):
                rc, out = await self.objstore.runner(
                    ["--config-dir", cfg, "--quiet", *args], {"MC_HOST_off": host}
                )
                if rc != 0:
                    raise OpsError(f"cópia externa: {redact(out, u.password or '')}")
            if (
                hashlib.sha256((Path(back) / "x.zip").read_bytes()).digest()
                != hashlib.sha256(src.read_bytes()).digest()
            ):
                raise OpsError("cópia externa lida de volta diverge do pacote")
        return f"{u.hostname}/{key}"

    async def _offsite_sharepoint(self, url: str, app: str, env: str, name: str) -> str:
        """`sharepoint://<connection>[/folder]`: uploads the package through an existing SharePoint connection
        (Administration › Connections; credentials stay in the vault) and downloads it back checking the sha256."""
        conn, _, folder = url.removeprefix("sharepoint://").partition("/")
        c = self.conns.get(conn) if self.conns else None
        if not c or c.get("kind") != "sharepoint":
            raise OpsError(f"cópia externa: conexão SharePoint {conn!r} não encontrada")
        sub = app if env != "staging" else f"{app}__staging"
        rel, src = "/".join(p for p in (*folder.split("/"), sub, name) if p), self.root / sub / name
        data = src.read_bytes()

        async def feed() -> AsyncIterator[bytes]:
            for i in range(0, len(data), 4 << 20):
                yield data[i : i + (4 << 20)]

        client = self.conns.backend(c)
        try:
            await client.upload(rel, len(data), "application/zip", Body(feed()))
            _, _, body = await client.download(rel)
            back = b"".join([chunk async for chunk in body])
        except StorageError as e:
            raise OpsError(f"cópia externa (SharePoint): {e.admin_text()}") from e
        if hashlib.sha256(back).digest() != hashlib.sha256(data).digest():
            raise OpsError("cópia externa lida de volta diverge do pacote")
        return f"SharePoint {conn}/{rel}"

    async def _restore_bucket(self, bucket: str, files: dict[str, bytes]) -> str:
        with tempfile.TemporaryDirectory(prefix="hub-rb-") as tmp:
            for n, b in files.items():
                if n.startswith("bucket/"):
                    p = Path(tmp) / n[7:]
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_bytes(b)
            rc, out = await self.objstore.mc("mirror", "--overwrite", tmp, f"hub/{bucket}")
        if rc != 0:
            raise OpsError(f"mc mirror para {bucket} falhou: {redact(out)}", 502)
        return f"bucket {bucket} restaurado"

    def prune(self, d: dict[str, Any], app: str, env: str, keep: int) -> list[str]:
        """Retention: keeps the `keep` newest completed backups of the app×environment; the others become `Expired`."""
        mine = [b for b in d["backups"] if b["app"] == app and (b.get("env") or "producao") == env
                and b["status"] == "Concluído" and b.get("file")]  # fmt: skip
        gone: list[str] = []
        for b in sorted(mine, key=lambda x: x["at"], reverse=True)[keep:]:
            p = self.root / (app if env != "staging" else f"{app}__staging") / str(b["file"])
            p.unlink(missing_ok=True)
            b["status"], b["file"] = "Expirado", None
            gone.append(f"#{b['id']}")
        return gone
