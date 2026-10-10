"""Exemplo de saga do SDK (onda 12, item 2.9): **cadastro documentado** de um ativo —
(1) cria o ativo, (2) confirma o documento enviado em duas fases (se o app tem a conexão
``ativos-docs`` vinculada), (3) notifica o solicitante pela Inbox do hub. Qualquer falha
definitiva (ou transitória na última tentativa) compensa em ordem inversa: remove o documento
confirmado e o ativo criado. Os efeitos no banco usam ``StepCtx.once`` (exatamente uma vez).

``POST /ativos/api/v1/assets/registrations`` enfileira a saga; ``falhar_em`` (nome de um passo)
força uma falha para demonstrar a compensação — só com ``HUB_DEMO_SAGA_FAULTS=1``.
"""

from __future__ import annotations

import os
from typing import Any

import asyncpg
from hub_sdk import outbox, storage
from hub_sdk.jobs import JobFail, JobRetry, JobSpec
from hub_sdk.saga import Saga, StepCtx
from hub_sdk.uploads import Uploads

KIND = "ativos.cadastro_documentado"
# Conexão de armazenamento dos documentos (vinculada em Aplicações › Conexões).
DOCS = "ativos-docs"
SPEC = JobSpec(KIND, max_attempts=4, max_duration=300)


class LazyPool:
    """Pool do SDK resolvido na hora do uso (o handler é criado antes do banco abrir)."""

    def __init__(self) -> None:
        self.sdk: Any = None

    def acquire(self) -> Any:
        return self.sdk.pool.acquire()


def uploads_for(pool: Any) -> Uploads | None:
    conn = storage.connection(DOCS)
    return Uploads(pool, conn) if conn is not None else None


def _injected(c: StepCtx) -> None:
    if os.environ.get("HUB_DEMO_SAGA_FAULTS") == "1" and (c.input or {}).get("falhar_em") == c.step:
        raise JobFail(f"falha simulada em {c.step}")


async def _once_output(c: StepCtx, step: str) -> Any:
    """Saída gravada por ``once`` para um passo (a compensação de um passo que "falhou" depois
    de confirmar o efeito ainda acha o que desfazer)."""
    async with c.pool.acquire() as cl:
        r = await cl.fetchrow("SELECT output FROM hub_saga_once WHERE key = $1", f"{c.saga_id}:{step}")
    return (r["output"] if r else None) or None


def build(pool: Any) -> Saga:
    async def criar(c: StepCtx) -> Any:
        _injected(c)
        inp = c.input or {}
        tag, name, cat, loc, st = (
            str(inp.get(k) or "").strip() for k in ("tag", "name", "category", "location", "status")
        )
        if not tag or not name:
            raise JobFail("tag e name são obrigatórios")

        async def fx(conn: Any) -> Any:
            try:
                aid = await conn.fetchval(
                    "INSERT INTO assets (tag, name, category, location, status) VALUES ($1,$2,$3,$4,$5) RETURNING id",
                    tag,
                    name,
                    cat,
                    loc,
                    st,
                )
            except asyncpg.UniqueViolationError as e:
                raise JobFail("patrimônio já cadastrado") from e
            except (asyncpg.PostgresError, OSError) as e:
                raise JobRetry(str(e)) from e
            return {"asset": aid}

        return await c.once(fx)

    async def criar_undo(c: StepCtx) -> Any:
        out = c.output if c.output else await _once_output(c, "criar")
        aid = (out or {}).get("asset")
        if isinstance(aid, int):
            async with c.pool.acquire() as cl:
                await cl.execute("DELETE FROM assets WHERE id = $1", aid)
        return None

    async def anexar(c: StepCtx) -> Any:
        _injected(c)
        inp = c.input or {}
        upload = inp.get("upload")
        if not upload:
            return {"documento": None}
        up = uploads_for(pool)
        if up is None:
            raise JobFail(f"sem a conexão {DOCS} vinculada para o documento")
        tag = str(inp.get("tag") or "sem-tag")
        file = str(inp.get("file_name") or "documento")

        # Um segmento só (o destino nunca escapa de ativos/<tag>/).
        def seg(v: str) -> bool:
            return bool(v) and "/" not in v and "\\" not in v and v not in (".", "..")

        if not seg(tag) or not seg(file):
            raise JobFail("tag/arquivo inválidos para o documento")
        path = f"ativos/{tag}/{file}"
        user = c.user  # o envio precisa ser do mesmo usuário que pediu a saga

        async def fx(conn: Any) -> Any:
            try:
                done = await up.commit(conn, str(upload), path, user)
            except (ValueError, storage.StorageError) as e:
                raise JobFail(str(e)) from e
            return {"upload": done.id, "path": done.path}

        return await c.once(fx)

    async def anexar_undo(c: StepCtx) -> Any:
        out = c.output if c.output else await _once_output(c, "anexar")
        up = uploads_for(pool)
        uid = (out or {}).get("upload")
        if up is None or not uid:
            return None
        # Documento confirmado por esta saga: cancela sob a trava da linha (serializado com a
        # promoção) e apaga staging e destino.
        try:
            await up.cancel(str(uid))
        except Exception as e:
            raise JobRetry(str(e)) from e
        return None

    async def notificar(c: StepCtx) -> Any:
        _injected(c)
        user = c.user
        tag = str((c.input or {}).get("tag") or "")

        async def fx(conn: Any) -> Any:
            ev = outbox.notification(
                user,
                "Ativo cadastrado com documento",
                body=f"O ativo {tag} foi cadastrado.",
                link="/apps/ativos/assets",
                to_users=[user],
            )
            return {"event": await outbox.write(conn, ev)}

        return await c.once(fx)

    return (
        Saga(SPEC)
        .step("criar", criar, criar_undo)
        .step("anexar", anexar, anexar_undo)
        .step("notificar", notificar)
    )
