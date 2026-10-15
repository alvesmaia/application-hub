"""Backups: read-only credentials (`hub_bkp_<db>`, `bkp-<bucket>` in the vault), zip package (database + bucket + volumes) and restore.

Port of `hub-ops::creds` and the essentials of `dr`: `pg_dump`/`psql` run in the Postgres container (`docker exec`),
the bucket via `mc mirror`. Database restore in non-transactional mode (the current one goes "aside" and comes back if it fails),
optional off-site copy (`HUB_BACKUP_OFFSITE_URL`) and the list of side databases; the scheduler is in `scheduler.py`."""

# ruff: noqa: S608 - admin SQL built with escaped literals/identifiers (`_lit`/`_qi`)
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import tempfile
import zipfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from hub.connections.provision import ObjectStore
from hub.ops.backup_db import _BUCKET, _DB, SYSTEM_DBS
from hub.ops.backup_db import _lit as _lit
from hub.ops.backup_db import _qi as _qi
from hub.ops.backup_db import redact as redact
from hub.ops.backup_offsite import BackupOffsite
from hub.ops.model import OpsError, OpsStore, now_ms
from hub.ops.runtime import Engine, exec_in

ROLE_FILE = "/tmp/hub-role.sql"  # noqa: S108 - inside the Postgres container
PG_ROLE_PREFIX, S3_BACKUP_PREFIX = "hub_bkp_", "bkp-"
Progress = Callable[[int, str], None]


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


def _collect(tmp: str) -> dict[str, bytes]:
    return {
        f"bucket/{p.relative_to(tmp).as_posix()}": p.read_bytes() for p in Path(tmp).rglob("*") if p.is_file()
    }


def parse_targets(env_vars: dict[str, str]) -> tuple[str | None, str | None]:
    """(database, bucket) of the app from the injected variables (`HUB_DB_URL`, `HUB_S3_BUCKET`)."""
    url = env_vars.get("HUB_DB_URL", "")
    db = urlsplit(url).path.lstrip("/") if url else ""
    return (db or None), (env_vars.get("HUB_S3_BUCKET") or None)


class Backups(BackupOffsite):
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

    async def _dump_db(self, db: str, progress: Progress) -> bytes:
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
        return out

    def _add_volume(self, app: str, vol: str, blobs: dict[str, bytes], parts: list[dict[str, Any]]) -> None:
        base = self._volume_dir(app, vol)
        if not base.is_dir():
            return
        for f in sorted(p for p in base.rglob("*") if p.is_file()):
            blobs[f"files/{vol}/{f.relative_to(base).as_posix()}"] = f.read_bytes()
        parts.append({"kind": "files", "name": vol})

    async def _collect_parts(
        self, app: str, targets: tuple[str | None, str | None], volumes: list[str], progress: Progress
    ) -> tuple[list[dict[str, Any]], dict[str, bytes], list[str]]:
        """(parts, blobs, notes): the database dump, the bucket mirror and the volumes' files."""
        db, bucket = targets
        parts: list[dict[str, Any]] = []
        blobs: dict[str, bytes] = {}
        notes: list[str] = []
        if db:
            blobs[f"db/{db}.sql"] = await self._dump_db(db, progress)
            parts.append({"kind": "db", "name": db})
        else:
            notes.append("app sem banco (HUB_DB_URL)")
        if bucket:
            progress(60, f"Copiando o bucket {bucket} (mc mirror)")
            blobs.update(await self._mirror_bucket(bucket))
            parts.append({"kind": "bucket", "name": bucket})
        for vol in volumes:
            self._add_volume(app, vol, blobs, parts)
        return parts, blobs, notes

    def _write_zip(
        self, app: str, env: str, name: str, man: dict[str, Any], blobs: dict[str, bytes]
    ) -> bytes:
        d = self.root / (app if env != "staging" else f"{app}{'__'}staging")
        d.mkdir(parents=True, exist_ok=True)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("manifest.json", json.dumps(man, ensure_ascii=False))
            for n, b in blobs.items():
                z.writestr(n, b)
        (d / name).write_bytes(buf.getvalue())
        return buf.getvalue()

    async def create_bundle(  # noqa: PLR0915
        self, app: str, env: str, volumes: list[str], hooks: bool, progress: Progress
    ) -> tuple[str, str, dict[str, Any]]:  # fmt: skip
        """Writes `<data>/backups/<app>/<app>-<ms>.zip`; returns (name, sha256, manifest)."""
        started = now_ms()
        targets = parse_targets(await self.app_env(app, env))
        paused = False
        if hooks and self.hook:
            progress(10, "Pausando gravações do app (hook pre)")
            await self.hook(app, "pre")
            paused = True
        try:
            parts, blobs, notes = await self._collect_parts(app, targets, volumes, progress)
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
        name = f"{app}-{started}.zip"
        raw = self._write_zip(app, env, name, man, blobs)
        man["summary"] = " · ".join(
            f"{'banco' if p['kind'] == 'db' else 'bucket' if p['kind'] == 'bucket' else 'volume'} {p['name']}"
            for p in parts
        )
        return name, hashlib.sha256(raw).hexdigest(), man

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

    async def restore_bundle(  # noqa: C901
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

    async def clone_to_staging(  # noqa: C901, PLR0915
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
