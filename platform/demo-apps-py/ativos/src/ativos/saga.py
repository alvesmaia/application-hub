"""SDK saga example (wave 12, item 2.9): **documented registration** of an asset —
(1) creates the asset, (2) confirms the document sent in two phases (if the app has the
``ativos-docs`` connection linked), (3) notifies the requester through the hub Inbox. Any
definitive failure (or transient on the last attempt) compensates in reverse order: removes the
confirmed document and the created asset. DB effects use ``StepCtx.once`` (exactly once).

``POST /ativos/api/v1/assets/registrations`` enqueues the saga; ``falhar_em`` (name of a step)
forces a failure to demonstrate the compensation — only with ``HUB_DEMO_SAGA_FAULTS=1``.
"""

from __future__ import annotations

import os
from typing import Any

import asyncpg
from hub_sdk import base, outbox, storage
from hub_sdk.jobs import JobFail, JobRetry, JobSpec
from hub_sdk.saga import Saga, StepCtx
from hub_sdk.uploads import Uploads

KIND = "ativos.cadastro_documentado"
# Storage connection for the documents (linked in Applications › Connections).
DOCS = "ativos-docs"
SPEC = JobSpec(KIND, max_attempts=4, max_duration=300)


class LazyPool:
    """SDK pool resolved at use time (the handler is created before the DB opens)."""

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
    """Output written by ``once`` for a step (compensating a step that "failed" after confirming the
    effect still finds what to undo)."""
    async with c.pool.acquire() as cl:
        r = await cl.fetchrow("SELECT output FROM hub_saga_once WHERE key = $1", f"{c.saga_id}:{step}")
    return (r["output"] if r else None) or None


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


def _seg(v: str) -> bool:
    """A single path segment (the target never escapes ativos/<tag>/)."""
    return bool(v) and "/" not in v and "\\" not in v and v not in (".", "..")


class Docs:
    """The document steps, which need the app pool for the two-phase uploads."""

    def __init__(self, pool: Any) -> None:
        self.pool = pool

    async def anexar(self, c: StepCtx) -> Any:
        _injected(c)
        inp = c.input or {}
        upload = inp.get("upload")
        if not upload:
            return {"documento": None}
        up = uploads_for(self.pool)
        if up is None:
            raise JobFail(f"sem a conexão {DOCS} vinculada para o documento")
        tag = str(inp.get("tag") or "sem-tag")
        file = str(inp.get("file_name") or "documento")
        if not _seg(tag) or not _seg(file):
            raise JobFail("tag/arquivo inválidos para o documento")
        path = f"ativos/{tag}/{file}"
        user = c.user  # the upload must come from the same user who requested the saga

        async def fx(conn: Any) -> Any:
            try:
                done = await up.commit(conn, str(upload), path, user)
            except (ValueError, storage.StorageError) as e:
                raise JobFail(str(e)) from e
            return {"upload": done.id, "path": done.path}

        return await c.once(fx)

    async def anexar_undo(self, c: StepCtx) -> Any:
        out = c.output if c.output else await _once_output(c, "anexar")
        up = uploads_for(self.pool)
        uid = (out or {}).get("upload")
        if up is None or not uid:
            return None
        # Document confirmed by this saga: cancels under the row lock (serialized with the
        # promotion) and deletes staging and target.
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
            link=f"{base('ativos')}/assets",
            to_users=[user],
        )
        return {"event": await outbox.write(conn, ev)}

    return await c.once(fx)


def build(pool: Any) -> Saga:
    docs = Docs(pool)
    return (
        Saga(SPEC)
        .step("criar", criar, criar_undo)
        .step("anexar", docs.anexar, docs.anexar_undo)
        .step("notificar", notificar)
    )
