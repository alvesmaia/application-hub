"""Storage broker: the only path from apps to the connections (`/_hub/storage/*`).

Serves on a separate address from the shell (`HUB_BROKER_ADDR`; without the variable it stays off) and authenticates with the token
managed per app × environment (`HUB_STORAGE_TOKEN`). The app never receives the secret: the hub runs the operation with the
stored credential, confined to the base folder. Every operation of an authenticated app is audited."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any, cast

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from hub.admin.store import AdminStore
from hub.connections.broker_ctx import BrokerCtx, bctx, bearer, clip, fail
from hub.connections.broker_ctx import Caller as Caller
from hub.connections.broker_ctx import parse_cidrs as parse_cidrs
from hub.connections.broker_ctx import source_allowed as source_allowed
from hub.connections.broker_ctx import who_of as who_of
from hub.connections.sql import SQL_KINDS, SqlBackend, SqlError, sql_hash
from hub.connections.store import ConnError, ConnStore, link_policy
from hub.storage import Body, StorageError, clean_file

router = APIRouter()


@router.get("/healthz")
async def healthz() -> Response:
    return Response("ok")


@router.get("/_hub/storage")
async def bound(request: Request, b: BrokerCtx = Depends(bctx)) -> Response:
    owner = await b.store.token_owner(bearer(request))
    if owner is None:
        return fail(401, "Token do broker ausente ou inválido.")
    items = [
        {
            "name": c["name"],
            "kind": c["kind"],
            "ops": c["allowed_ops"],
            "max_rows": c["max_rows"],
            "readonly": c["readonly"],
        }
        if c["kind"] in SQL_KINDS
        else {
            "name": c["name"],
            "kind": c["kind"],
            "ops": c["allowed_ops"],
            "max_upload_mb": c["max_upload_mb"],
            "max_link_hours": c["max_link_hours"],
        }
        for c in b.store.all()
        if any(b["app"] == owner[0] and b["env"] == owner[1] for b in c["bindings"])
    ]
    return JSONResponse({"app": owner[0], "env": owner[1], "connections": items})


@router.get("/_hub/storage/{name}/list")
async def list_(request: Request, name: str, prefix: str = "", b: BrokerCtx = Depends(bctx)) -> Response:
    c = await b.authorize(request, name, "listar", ("list",))
    if isinstance(c, JSONResponse):
        return c
    shown = clip(prefix or "/", 200)
    try:
        items = await c.backend.ls(prefix)
    except StorageError as e:
        return await c.storage_fail("listar", shown, e)
    c.release()
    await c.ok("listar", f"{shown} · {len(items)} itens")
    return JSONResponse({"items": [i.json() for i in items]})


@router.get("/_hub/storage/{name}/meta")
async def meta(request: Request, name: str, path: str = "", b: BrokerCtx = Depends(bctx)) -> Response:
    c = await b.authorize(request, name, "metadados", ("list", "download"))
    if isinstance(c, JSONResponse):
        return c
    try:
        item = await c.backend.meta(path)
    except StorageError as e:
        return await c.storage_fail("metadados", clip(path, 200), e)
    c.release()
    await c.ok("metadados", clip(path, 200))
    return JSONResponse(item.json())


@router.get("/_hub/storage/{name}/content")
async def download(request: Request, name: str, path: str = "", b: BrokerCtx = Depends(bctx)) -> Response:
    c = await b.authorize(request, name, "baixar", ("download",))
    if isinstance(c, JSONResponse):
        return c
    try:
        ctype, length, stream = await c.backend.download(path)
    except StorageError as e:
        return await c.storage_fail("baixar", clip(path, 200), e)
    await c.ok("baixar", f"{clip(path, 200)} · {length if length is not None else '?'} bytes")

    async def body() -> AsyncIterator[bytes]:
        try:
            async for chunk in stream:
                yield cast(bytes, chunk)
        finally:
            c.release()  # the app's slot is only released when the body ends (or the app gives up)

    headers = {"x-content-type-options": "nosniff", "cache-control": "no-store"}
    if length is not None:
        headers["content-length"] = str(length)
    return StreamingResponse(body(), media_type=ctype, headers=headers)


def _declared_length(request: Request) -> int | None:
    raw_len = request.headers.get("content-length", "")
    return int(raw_len) if raw_len.isdigit() else None


def _upload_ctype(request: Request) -> str:
    ctype = request.headers.get("content-type", "")
    if not ctype or len(ctype) > 200 or "\r" in ctype or "\n" in ctype:
        return "application/octet-stream"
    return ctype


async def _limited(request: Request, length: int) -> AsyncIterator[bytes]:
    seen = 0
    async for chunk in request.stream():
        seen += len(chunk)
        if seen > length:
            raise StorageError("invalid", "O corpo é maior que o tamanho declarado (Content-Length).")
        yield chunk


@router.put("/_hub/storage/{name}/content")
async def upload(request: Request, name: str, path: str = "", b: BrokerCtx = Depends(bctx)) -> Response:
    c = await b.authorize(request, name, "enviar", ("upload",))
    if isinstance(c, JSONResponse):
        return c
    shown = clip(path, 200)
    length = _declared_length(request)
    if length is None:
        return await c.fail(
            "enviar",
            shown,
            411,
            "Envie o Content-Length (o hub não guarda o arquivo inteiro na memória).",
        )
    if length > c.conn["max_upload_mb"] * 1024 * 1024:
        return await c.fail(
            "enviar",
            f"{shown} · {length} bytes",
            413,
            f"Arquivo acima do limite desta conexão ({c.conn['max_upload_mb']} MB).",
        )
    ctype = _upload_ctype(request)
    try:
        clean_file(path)  # path validated before reading the body
    except StorageError as e:
        return await c.storage_fail("enviar", shown, e)
    detail = f"{shown} · {length} bytes · {ctype}"
    try:
        item = await c.backend.upload(path, length, ctype, Body(_limited(request, length)))
    except StorageError as e:
        return await c.storage_fail("enviar", detail, e)
    c.release()
    await c.ok("enviar", detail)
    return JSONResponse(item.json(), status_code=201)


@router.delete("/_hub/storage/{name}/content")
async def delete(request: Request, name: str, path: str = "", b: BrokerCtx = Depends(bctx)) -> Response:
    c = await b.authorize(request, name, "excluir", ("delete",))
    if isinstance(c, JSONResponse):
        return c
    try:
        await c.backend.delete(path)
    except StorageError as e:
        return await c.storage_fail("excluir", clip(path, 200), e)
    c.release()
    await c.ok("excluir", clip(path, 200))
    return Response(status_code=204)


@router.post("/_hub/storage/{name}/move")
async def move(request: Request, name: str, b: BrokerCtx = Depends(bctx)) -> Response:
    c = await b.authorize(request, name, "mover", ("move",))
    if isinstance(c, JSONResponse):
        return c
    try:
        m = json.loads(await request.body())
        src, dst = str(m["from"]), str(m["to"])
    except (ValueError, KeyError, TypeError):
        return await c.fail("mover", "corpo inválido", 400, 'Corpo: {"from", "to"}.')
    detail = f"{clip(src, 200)} → {clip(dst, 200)}"
    try:
        item = await c.backend.move(src, dst)
    except StorageError as e:
        return await c.storage_fail("mover", detail, e)
    c.release()
    await c.ok("mover", detail)
    return JSONResponse(item.json())


@router.post("/_hub/storage/{name}/share-link")
async def share_link(request: Request, name: str, b: BrokerCtx = Depends(bctx)) -> Response:
    c = await b.authorize(request, name, "gerar link", ("share_link",))
    if isinstance(c, JSONResponse):
        return c
    try:
        body = json.loads(await request.body())
        path, kind = str(body["path"]), str(body.get("kind") or "view")
        raw_hours = body.get("expires_hours")
        hours = int(raw_hours) if raw_hours is not None else min(24, max(c.conn["max_link_hours"], 1))
    except (ValueError, KeyError, TypeError):
        return await c.fail("gerar link", "corpo inválido", 400, 'Corpo: {"path", "kind", "expires_hours"}.')
    asked = f"{clip(path, 200)} · {clip(kind, 10)} · {hours} h"
    try:
        scope = link_policy(c.conn, c.app, kind, hours)
    except ConnError as e:
        return await c.fail("gerar link", asked, 403 if "não liberad" in str(e) else 400, str(e))
    try:
        link = await c.backend.share_link(path, kind, scope, hours * 3600)
    except StorageError as e:
        return await c.storage_fail("gerar link", f"{asked} · {scope}", e)
    c.release()
    await c.ok("gerar link", f"{asked} · {scope} · expira {link.get('expires') or '?'}")
    return JSONResponse(link)


@router.post("/_hub/sql/{name}/query")
async def sql_query(request: Request, name: str, b: BrokerCtx = Depends(bctx)) -> Response:
    """SQL query with the hub's credential. Audit: SQL hash/size, rows and time — never
    data nor parameter values."""
    c = await b.authorize(request, name, "consulta SQL", ("query",))
    if isinstance(c, JSONResponse):
        return c
    try:
        body = json.loads(await request.body())
        sql, params, max_rows = body["sql"], body.get("params") or {}, body.get("max_rows")
        if not isinstance(sql, str) or not isinstance(params, dict):
            raise TypeError
        if max_rows is not None and (not isinstance(max_rows, int) or max_rows < 1):
            raise TypeError
    except (ValueError, KeyError, TypeError):
        return await c.fail("consulta SQL", "corpo inválido", 400, 'Corpo: {"sql", "params", "max_rows"}.')
    shown = f"sql {sql_hash(sql)} ({len(sql)} caracteres, {len(params)} parâmetros)"
    if not isinstance(c.backend, SqlBackend):
        return await c.fail("consulta SQL", shown, 400, "Esta conexão não é de banco de dados.")
    try:
        res = await c.backend.query(sql, params, max_rows)
    except SqlError as e:
        return await c.fail("consulta SQL", shown, e.status, e.message)
    c.release()
    cut = " · truncada no limite" if res.truncated else ""
    await c.record("Consulta SQL", f"{c.conn['name']} · {shown} · {len(res.rows)} linhas{cut} · {res.ms} ms")
    return JSONResponse(res.json(), headers={"cache-control": "no-store"})


async def not_found(request: Request, exc: Any) -> Response:
    return fail(404, "Rota desconhecida.")


def build_broker(store: ConnStore, admin: AdminStore, key: bytes) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.broker = b = BrokerCtx(store, admin, key)
    app.middleware("http")(b.source_guard)
    app.include_router(router)
    app.add_exception_handler(404, not_found)
    return app


async def serve(app: FastAPI, addr: str) -> tuple[Any, asyncio.Task[None]]:
    """Starts the broker on `host:port` (uvicorn in the same event loop)."""
    import uvicorn

    host, _, port = addr.rpartition(":")
    server = uvicorn.Server(uvicorn.Config(app, host=host or "0.0.0.0", port=int(port), log_level="warning"))  # noqa: S104
    task = asyncio.create_task(server.serve())
    return server, task


async def stop(server: Any, task: asyncio.Task[None]) -> None:
    server.should_exit = True
    with contextlib.suppress(Exception):
        await asyncio.wait_for(task, 5)
