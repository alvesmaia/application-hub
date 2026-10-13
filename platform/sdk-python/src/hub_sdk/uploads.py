"""Envio de arquivos em **duas fases** (onda 12, item 2.9), com tabelas (``hub_uploads``) e tipo de tarefa de
promoção (``hub.upload.promote``).

1. :meth:`Uploads.begin` registra o envio (``pending``, com validade) e devolve a chave de
   staging (``_staging/<id>/<arquivo>``) e, no S3 direto, um ``PUT`` pré-assinado.
2. :meth:`Uploads.commit` roda **na transação do app** que grava o registro que aponta
   para o arquivo: marca ``committed`` com o caminho final e enfileira a promoção
   staging → final na mesma transação (tudo ou nada). Até a promoção,
   :meth:`Uploads.location` devolve a chave de staging.
3. Não confirmados vencem (``ttl``, padrão 24 h); :meth:`Uploads.cleanup` apaga o objeto
   dos vencidos e os órfãos do prefixo de staging (idempotente).
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import logging
import re
from dataclasses import dataclass
from typing import Any

from hub_sdk.jobs import JobCtx, JobFail, JobRetry, JobSpec, enqueue_with
from hub_sdk.schema import new_id
from hub_sdk.shutdown import Shutdown
from hub_sdk.storage import Connection, Item, S3Connection, StorageError, clean_path

log = logging.getLogger("hub_sdk.uploads")

PROMOTE_KIND = "hub.upload.promote"
STAGING_PREFIX = "_staging"
# Validade máxima da URL pré-assinada de envio (s); a do envio pode ser maior.
PUT_URL_TTL = 15 * 60
_NAME = re.compile(r"^[\w.\- ]{1,120}$")


def valid_name(n: str) -> bool:
    return bool(_NAME.match(n)) and not n.startswith(".") and not n.endswith(" ") and "/" not in n


def clean_final(p: str) -> str:
    """Caminho final normalizado e fora do prefixo de staging (sem diferenciar caixa)."""
    try:
        c = clean_path(p)
    except StorageError as e:
        raise ValueError("Caminho final inválido.") from e
    if not c or c.split("/", 1)[0].lower() == STAGING_PREFIX:
        raise ValueError("Caminho final inválido.")
    return c


def promote_spec() -> JobSpec:
    return JobSpec(kind=PROMOTE_KIND, max_attempts=10, max_duration=600.0)


@dataclass(frozen=True)
class UploadSession:
    id: str
    staging_path: str
    expires_at: int
    put_url: str | None = None


@dataclass(frozen=True)
class Committed:
    id: str
    path: str
    promote_job: str


@dataclass(frozen=True)
class CleanupReport:
    expired: int = 0
    orphans: int = 0


def _n(status: str) -> int:
    try:
        return int(str(status).rsplit(" ", 1)[-1])
    except ValueError:
        return 0


class Uploads:
    def __init__(
        self, pool: Any, store: Connection, ttl: float = 24 * 3600, max_size: int = 100 * 1024 * 1024
    ) -> None:
        self.pool = pool
        self.store = store
        self.ttl = max(60.0, float(ttl))
        self.max_size = max(1, int(max_size))

    async def begin(self, file_name: str, user: str) -> UploadSession:
        """Fase 1: registra o envio **em nome de** ``user`` e devolve a chave de staging. A URL
        pré-assinada vale no máximo :data:`PUT_URL_TTL` (e nunca além da validade do envio)."""
        if not valid_name(file_name):
            raise ValueError("Nome de arquivo inválido (letras, números, espaço, . _ -; até 120).")
        if not user.strip():
            raise ValueError("Envio sem usuário.")
        uid = new_id("upl")
        staging = f"{STAGING_PREFIX}/{uid}/{file_name}"
        async with self.pool.acquire() as c:
            exp = await c.fetchval(
                "INSERT INTO hub_uploads (id, connection, staging_path, user_name, expires_at) "
                "VALUES ($1, $2, $3, $4, now() + make_interval(secs => $5)) "
                "RETURNING (extract(epoch FROM expires_at) * 1000)::int8",
                uid,
                self.store.name,
                staging,
                user,
                self.ttl,
            )
        url = None
        if isinstance(self.store, S3Connection):
            secs = int(min(self.ttl, PUT_URL_TTL))
            url = self.store.presign_put_path(staging, secs, dt.datetime.now(dt.UTC))
        return UploadSession(id=uid, staging_path=staging, expires_at=int(exp), put_url=url)

    async def put(self, s: UploadSession, data: bytes, content_type: str) -> None:
        """Envia o conteúdo para a chave de staging (pelo broker/app)."""
        await self.store.upload(s.staging_path, data, content_type, len(data))

    async def commit(self, conn: Any, upload_id: str, final_path: str, user: str) -> Committed:
        """Fase 2 (na transação ``conn`` do app): confirma o envio **de** ``user`` (o do
        ``begin``, nesta conexão), registra tamanho e ETag do staging (a promoção só aceita
        essa versão) e enfileira a promoção. Falha se o envio não existe, é de outro
        usuário/conexão, venceu, já foi confirmado ou passa do tamanho máximo."""
        final = clean_final(final_path)
        staging = await conn.fetchval(
            "SELECT staging_path FROM hub_uploads WHERE id = $1 AND user_name = $2 AND connection = $3 "
            "AND state = 'pending' AND expires_at > now() FOR UPDATE",
            upload_id,
            user,
            self.store.name,
        )
        if staging is None:
            raise ValueError("Envio inexistente, de outro usuário, vencido ou já confirmado.")
        it = await self.store.meta(staging)
        if it.size > self.max_size:
            raise ValueError(f"Arquivo grande demais ({it.size} bytes; máximo {self.max_size}).")
        await conn.execute(
            "UPDATE hub_uploads SET state = 'committed', path = $2, committed_at = now(), size = $3, etag = $4 "
            "WHERE id = $1",
            upload_id,
            final,
            it.size,
            it.etag,
        )
        job, _ = await enqueue_with(conn, promote_spec(), {"upload": upload_id}, user, upload_id)
        return Committed(id=upload_id, path=final, promote_job=job.id)

    async def location(self, upload_id: str) -> str | None:
        """Onde o arquivo confirmado está agora (final depois da promoção; staging antes)."""
        async with self.pool.acquire() as c:
            r = await c.fetchrow(
                "SELECT staging_path, path, promoted_at IS NOT NULL AS promoted FROM hub_uploads "
                "WHERE id = $1 AND state = 'committed'",
                upload_id,
            )
        if r is None:
            return None
        return str(r["path"] or "") if r["promoted"] else str(r["staging_path"])

    async def cancel(self, upload_id: str) -> None:
        """Desfaz um envio (compensação) sob a trava da linha — serializado com a promoção:
        apaga o staging e, se já promovido, o destino, e marca ``cancelled``. Idempotente."""
        async with self.pool.acquire() as c, c.transaction():
            r = await c.fetchrow(
                "SELECT state, staging_path, path, promoted_at IS NOT NULL AS promoted FROM hub_uploads "
                "WHERE id = $1 FOR UPDATE",
                upload_id,
            )
            if r is None or r["state"] == "cancelled":
                return
            paths = [r["staging_path"]]
            owned = bool(r["promoted"])
            if not owned and r["state"] == "committed" and r["path"]:
                # Re-revisão P3: promoção que caiu depois do move e antes de marcar — staging
                # sumiu e o destino é a versão confirmada → também é deste envio.
                v = await c.fetchrow("SELECT size, etag FROM hub_uploads WHERE id = $1", upload_id)
                try:
                    await self.store.meta(r["staging_path"])
                except StorageError as e:
                    if e.status == 404:
                        try:
                            it = await self.store.meta(r["path"])
                            owned = it.size == v["size"] and (
                                v["etag"] is None
                                or it.etag is None
                                or v["etag"].strip('"') == it.etag.strip('"')
                            )
                        except StorageError:
                            owned = False
            if owned and r["path"]:
                paths.append(r["path"])
            for p in paths:
                try:
                    await self.store.delete(p)
                except StorageError as e:
                    if e.status != 404:
                        raise
            await c.execute("UPDATE hub_uploads SET state = 'cancelled' WHERE id = $1", upload_id)

    async def promote(self, upload_id: str) -> None:
        """Promove (idempotente) sob a trava da linha: confere a versão confirmada
        (tamanho/ETag), não sobrescreve destino existente, move staging → final e confere."""
        async with self.pool.acquire() as c, c.transaction():
            r = await c.fetchrow(
                "SELECT state, staging_path, path, promoted_at IS NOT NULL AS promoted, size, etag "
                "FROM hub_uploads WHERE id = $1 FOR UPDATE",
                upload_id,
            )
            if r is None:
                raise JobFail("envio não encontrado")
            if r["state"] != "committed":
                raise JobFail(f"envio em estado {r['state']}")
            if r["promoted"]:
                return
            staging, dest = r["staging_path"], r["path"]

            def err(e: StorageError) -> Exception:
                return JobRetry(e.message) if e.transient or e.status >= 500 else JobFail(e.message)

            def same(it: Item) -> bool:
                if r["size"] is None or it.size != r["size"]:
                    return False
                a, b = r["etag"], it.etag
                return bool(a is None or b is None or a.strip('"') == b.strip('"'))

            try:
                cur = await self.store.meta(staging)
            except StorageError as e:
                if e.status != 404:
                    raise err(e) from e
                # Staging já não existe: a promoção anterior moveu e caiu antes de marcar.
                try:
                    it = await self.store.meta(dest)
                except StorageError as e2:
                    if e2.status == 404:
                        raise JobFail("o arquivo de staging sumiu antes da promoção") from e2
                    raise err(e2) from e2
                if not same(it):
                    raise JobFail("staging ausente e o destino não é a versão confirmada") from e
            else:
                if not same(cur):
                    raise JobFail("o arquivo de staging mudou depois da confirmação: recusado")
                try:
                    await self.store.meta(dest)
                    raise JobFail(f"o destino {dest} já existe: não sobrescrito")
                except StorageError as e:
                    if e.status != 404:
                        raise err(e) from e
                try:
                    await self.store.move(staging, dest)
                    moved = await self.store.meta(dest)
                except StorageError as e:
                    raise err(e) from e
                if not same(moved):
                    with contextlib.suppress(StorageError):
                        await self.store.delete(dest)
                    raise JobFail("o arquivo mudou durante a promoção: removido")
            await c.execute("UPDATE hub_uploads SET promoted_at = now() WHERE id = $1", upload_id)

    def promote_handler(self) -> PromoteHandler:
        return PromoteHandler(self)

    async def cleanup(self) -> CleanupReport:
        """Apaga envios vencidos (objeto + ``expired``) e órfãos do prefixo de staging."""
        expired = orphans = 0
        async with self.pool.acquire() as c:
            rows = await c.fetch(
                "SELECT id, staging_path FROM hub_uploads WHERE state = 'pending' AND expires_at < now() "
                "AND connection = $1 LIMIT 500",
                self.store.name,
            )
            for r in rows:
                try:
                    await self.store.delete(r["staging_path"])
                except StorageError as e:
                    if e.status != 404:
                        raise
                expired += _n(
                    await c.execute(
                        "UPDATE hub_uploads SET state = 'expired' WHERE id = $1 AND state = 'pending'",
                        r["id"],
                    )
                )
            try:
                items = await self.store.list(STAGING_PREFIX)
            except StorageError as e:
                if e.status != 404:
                    raise
                items = []
            for it in items:
                rel = it.path.lstrip("/")
                parts = (
                    rel[len(STAGING_PREFIX) + 1 :].split("/") if rel.startswith(STAGING_PREFIX + "/") else []
                )
                if not parts or not parts[0]:
                    continue
                uid = parts[0]
                if it.folder:
                    try:
                        files = [
                            x.path for x in await self.store.list(f"{STAGING_PREFIX}/{uid}") if not x.folder
                        ]
                    except StorageError:
                        files = []
                else:
                    files = [it.path]
                keep = await c.fetchval(
                    "SELECT 1 FROM hub_uploads WHERE id = $1 AND (state = 'pending' OR "
                    "(state = 'committed' AND promoted_at IS NULL))",
                    uid,
                )
                if keep:
                    continue
                for f in files:
                    try:
                        await self.store.delete(f.lstrip("/"))
                        orphans += 1
                    except StorageError as e:
                        if e.status != 404:
                            raise
        return CleanupReport(expired=expired, orphans=orphans)

    def spawn_cleanup(self, every: float, shutdown: Shutdown) -> asyncio.Task[None]:
        async def loop() -> None:
            while not shutdown.triggered:
                try:
                    await asyncio.wait_for(shutdown.wait(), every)
                    return
                except TimeoutError:
                    pass
                try:
                    await self.cleanup()
                except Exception:
                    log.exception("limpeza de envios")

        return asyncio.create_task(loop())


class PromoteHandler:
    """Tarefa ``hub.upload.promote`` (registre junto com os outros handlers)."""

    def __init__(self, uploads: Uploads) -> None:
        self.uploads = uploads
        self.spec = promote_spec()

    async def run(self, ctx: JobCtx) -> Any:
        p = ctx.job.payload
        uid = str((p if isinstance(p, dict) else {}).get("upload") or "")
        await self.uploads.promote(uid)
        return {"upload": uid, "promoted": True}

    def final_event(self, job: Any, status: str, detail: str) -> None:
        return None
