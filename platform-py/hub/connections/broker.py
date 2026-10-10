"""Broker de armazenamento: o único caminho dos apps até as conexões (`/_hub/storage/*`).

Serve num endereço separado do shell (`HUB_BROKER_ADDR`; sem a variável fica desligado) e autentica com o token
gerido por app × ambiente (`HUB_STORAGE_TOKEN`). O app nunca recebe o segredo: o hub executa a operação com a
credencial guardada, confinada à pasta-base. Toda operação de um app autenticado é auditada."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import ipaddress
import json
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from hub.admin.config import env_label
from hub.admin.store import AdminStore
from hub.connections.sql import SQL_KINDS, SqlBackend, SqlError, sql_hash
from hub.connections.store import ConnError, ConnStore, link_policy
from hub.gateway.identity import USER_SIG_HEADER, verify_user
from hub.storage import Body, StorageError, clean_file


def per_app_limit() -> int:
    try:
        return max(1, int(os.getenv("HUB_BROKER_PER_APP", "8")))
    except ValueError:
        return 8


def clip(s: str, n: int) -> str:
    return "".join(c for c in s if c.isprintable())[:n]


def fail(status: int, msg: str, retry_after: int | None = None) -> JSONResponse:
    headers = {"cache-control": "no-store"} | ({"retry-after": str(retry_after)} if retry_after else {})
    return JSONResponse({"error": msg}, status_code=status, headers=headers)


def storage_fail(e: StorageError) -> JSONResponse:
    return fail(e.status, e.message, max(1, e.retry_after or 2) if e.kind == "unavailable" else None)


def who_of(headers: Any, key: bytes, app: str, env: str, now: int | None = None) -> tuple[str, str]:
    """(autor, nota): o usuário só vira autor com a assinatura do gateway válida para ESTE app e ambiente."""
    raw = headers.get("x-hub-user")
    claimed = ""
    if raw:
        try:
            body = base64.urlsafe_b64decode(
                raw.strip().rstrip("=") + "=" * (-len(raw.strip().rstrip("=")) % 4)
            )
            user = json.loads(body).get("user")
            claimed = clip(user, 120) if isinstance(user, str) else ""
        except ValueError:
            claimed = ""
    if not claimed:
        return f"app:{app}", ""
    sig = headers.get(USER_SIG_HEADER)
    if raw and sig and verify_user(key, app, env, raw, sig, now):
        return claimed, ""
    return f"app:{app}", f" · usuário informado pelo app (não verificado): {claimed}"


def parse_cidrs(v: str) -> dict[str, list[ipaddress.IPv4Network]]:
    out: dict[str, list[ipaddress.IPv4Network]] = {}
    for part in v.split(","):
        app, _, cidr = part.strip().partition("=")
        try:
            net = ipaddress.IPv4Network(cidr.strip(), strict=False)
        except ValueError:
            continue
        out.setdefault(app.strip(), []).append(net)
    return out


def source_allowed(cidrs: dict[str, list[ipaddress.IPv4Network]], app: str, peer: str | None) -> bool:
    """Vazia = sem checagem de origem; configurada, fecha (app fora da lista é recusado)."""
    if not cidrs:
        return True
    try:
        ip = ipaddress.ip_address(peer or "")
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is None:
            return False
        ip = ip.ipv4_mapped
    return any(ip in n for n in cidrs.get(app, []))


@dataclass
class Caller:
    app: str
    env: str
    actor: str
    note: str
    conn: dict[str, Any]
    backend: (
        Any  # GraphClient | S3Client nas rotas de arquivo (conexões SQL não passam da checagem de operação)
    )
    release: Any
    admin: AdminStore

    async def record(self, title: str, detail: str) -> None:
        await self.admin.audit(
            self.actor,
            title,
            f"{detail} · {env_label(self.env)}{self.note}",
            icon="variables",
            scope=self.app,
        )

    async def ok(self, op: str, detail: str) -> None:
        await self.record(f"Armazenamento: {op}", f"{self.conn['name']} · {detail}")

    async def deny(self, op: str, detail: str, status: int, why: str) -> None:
        await self.record(
            f"Armazenamento: {op} falhou",
            f"{self.conn['name']} · {detail} · HTTP {status} · {clip(why, 300)}",
        )

    async def fail(self, op: str, detail: str, status: int, msg: str) -> JSONResponse:
        self.release()
        await self.deny(op, detail, status, msg)
        return fail(status, msg)

    async def storage_fail(self, op: str, detail: str, e: StorageError) -> JSONResponse:
        self.release()
        await self.deny(
            op, detail, e.status, f"{e.message} — provedor: {e.detail}" if e.detail else e.message
        )
        return storage_fail(e)


def build_broker(store: ConnStore, admin: AdminStore, key: bytes) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    slots: dict[str, asyncio.Semaphore] = {}
    cidrs = parse_cidrs(os.getenv("HUB_BROKER_APP_CIDRS", ""))

    def bearer(request: Request) -> str:
        h = request.headers.get("authorization", "")
        return h[7:].strip() if h.startswith("Bearer ") else ""

    @app.middleware("http")
    async def source_guard(request: Request, call_next: Any) -> Response:
        if cidrs and (owner := await store.token_owner(bearer(request))):
            peer = request.client.host if request.client else None
            if not source_allowed(cidrs, owner[0], peer):
                actor, note = who_of(request.headers, key, owner[0], owner[1])
                why = (
                    "usado fora da rede do app"
                    if owner[0] in cidrs
                    else "app sem rede em HUB_BROKER_APP_CIDRS"
                )
                await admin.audit(
                    actor,
                    "Armazenamento: acesso negado",
                    f"token de {owner[0]} {why} ({peer or ''}) · {request.method} {clip(request.url.path, 120)} · "
                    f"{env_label(owner[1])}{note}",
                    icon="variables",
                    scope=owner[0],
                )
                return fail(403, "Origem não permitida para este token.")
        return cast(Response, await call_next(request))

    async def authorize(request: Request, name: str, op: str, need: tuple[str, ...]) -> Caller | JSONResponse:
        owner = await store.token_owner(bearer(request))
        if owner is None:
            return fail(401, "Token do broker ausente ou inválido.")
        a, env = owner
        actor, note = who_of(request.headers, key, a, env)

        async def record(title: str, detail: str) -> None:
            await admin.audit(actor, title, f"{detail} · {env_label(env)}{note}", icon="variables", scope=a)

        conn = store.get(name)
        if conn is None or not any(b["app"] == a and b["env"] == env for b in conn["bindings"]):
            await record(
                "Armazenamento: acesso negado", f'{op} · conexão "{clip(name, 60)}" não vinculada a {a}'
            )
            return fail(404, "Conexão não encontrada para este app.")
        if need and not any(o in conn["allowed_ops"] for o in need):
            await record(
                "Armazenamento: operação negada", f"{conn['name']} · {op} · {' ou '.join(need)} não liberada"
            )
            return fail(403, f'Operação "{need[0]}" não liberada nesta conexão.')
        sem = slots.setdefault(f"{a}/{env}", asyncio.Semaphore(per_app_limit()))
        if sem.locked():
            await record(
                "Armazenamento: limite de operações simultâneas", f"{conn['name']} · {op} · HTTP 429"
            )
            return fail(429, "Muitas operações de armazenamento simultâneas deste app.", 2)
        await sem.acquire()
        released = False

        def release() -> None:
            nonlocal released
            if not released:
                released = True
                sem.release()

        try:
            backend = store.backend(conn)
        except StorageError as e:
            release()
            await record(f"Armazenamento: {op} falhou", f"{conn['name']} · HTTP 502 · {clip(e.message, 300)}")
            return fail(502, "Conexão indisponível no hub (configuração).")
        return Caller(a, env, actor, note, conn, backend, release, admin)

    @app.get("/healthz")
    async def healthz() -> Response:
        return Response("ok")

    @app.get("/_hub/storage")
    async def bound(request: Request) -> Response:
        owner = await store.token_owner(bearer(request))
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
            for c in store.all()
            if any(b["app"] == owner[0] and b["env"] == owner[1] for b in c["bindings"])
        ]
        return JSONResponse({"app": owner[0], "env": owner[1], "connections": items})

    @app.get("/_hub/storage/{name}/list")
    async def list_(request: Request, name: str, prefix: str = "") -> Response:
        c = await authorize(request, name, "listar", ("list",))
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

    @app.get("/_hub/storage/{name}/meta")
    async def meta(request: Request, name: str, path: str = "") -> Response:
        c = await authorize(request, name, "metadados", ("list", "download"))
        if isinstance(c, JSONResponse):
            return c
        try:
            item = await c.backend.meta(path)
        except StorageError as e:
            return await c.storage_fail("metadados", clip(path, 200), e)
        c.release()
        await c.ok("metadados", clip(path, 200))
        return JSONResponse(item.json())

    @app.get("/_hub/storage/{name}/content")
    async def download(request: Request, name: str, path: str = "") -> Response:
        c = await authorize(request, name, "baixar", ("download",))
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
                c.release()  # a vaga do app só volta quando o corpo termina (ou o app desiste)

        headers = {"x-content-type-options": "nosniff", "cache-control": "no-store"}
        if length is not None:
            headers["content-length"] = str(length)
        return StreamingResponse(body(), media_type=ctype, headers=headers)

    @app.put("/_hub/storage/{name}/content")
    async def upload(request: Request, name: str, path: str = "") -> Response:
        c = await authorize(request, name, "enviar", ("upload",))
        if isinstance(c, JSONResponse):
            return c
        shown = clip(path, 200)
        raw_len = request.headers.get("content-length", "")
        if not raw_len.isdigit():
            return await c.fail(
                "enviar",
                shown,
                411,
                "Envie o Content-Length (o hub não guarda o arquivo inteiro na memória).",
            )
        length = int(raw_len)
        if length > c.conn["max_upload_mb"] * 1024 * 1024:
            return await c.fail(
                "enviar",
                f"{shown} · {length} bytes",
                413,
                f"Arquivo acima do limite desta conexão ({c.conn['max_upload_mb']} MB).",
            )
        ctype = request.headers.get("content-type", "")
        if not ctype or len(ctype) > 200 or "\r" in ctype or "\n" in ctype:
            ctype = "application/octet-stream"
        try:
            clean_file(path)  # caminho validado antes de ler o corpo
        except StorageError as e:
            return await c.storage_fail("enviar", shown, e)
        detail = f"{shown} · {length} bytes · {ctype}"

        async def limited() -> AsyncIterator[bytes]:
            seen = 0
            async for chunk in request.stream():
                seen += len(chunk)
                if seen > length:
                    raise StorageError("invalid", "O corpo é maior que o tamanho declarado (Content-Length).")
                yield chunk

        try:
            item = await c.backend.upload(path, length, ctype, Body(limited()))
        except StorageError as e:
            return await c.storage_fail("enviar", detail, e)
        c.release()
        await c.ok("enviar", detail)
        return JSONResponse(item.json(), status_code=201)

    @app.delete("/_hub/storage/{name}/content")
    async def delete(request: Request, name: str, path: str = "") -> Response:
        c = await authorize(request, name, "excluir", ("delete",))
        if isinstance(c, JSONResponse):
            return c
        try:
            await c.backend.delete(path)
        except StorageError as e:
            return await c.storage_fail("excluir", clip(path, 200), e)
        c.release()
        await c.ok("excluir", clip(path, 200))
        return Response(status_code=204)

    @app.post("/_hub/storage/{name}/move")
    async def move(request: Request, name: str) -> Response:
        c = await authorize(request, name, "mover", ("move",))
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

    @app.post("/_hub/storage/{name}/share-link")
    async def share_link(request: Request, name: str) -> Response:
        c = await authorize(request, name, "gerar link", ("share_link",))
        if isinstance(c, JSONResponse):
            return c
        try:
            body = json.loads(await request.body())
            path, kind = str(body["path"]), str(body.get("kind") or "view")
            raw_hours = body.get("expires_hours")
            hours = int(raw_hours) if raw_hours is not None else min(24, max(c.conn["max_link_hours"], 1))
        except (ValueError, KeyError, TypeError):
            return await c.fail(
                "gerar link", "corpo inválido", 400, 'Corpo: {"path", "kind", "expires_hours"}.'
            )
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

    @app.post("/_hub/sql/{name}/query")
    async def sql_query(request: Request, name: str) -> Response:
        """Consulta SQL com a credencial do hub. Auditoria: hash/tamanho do SQL, linhas e tempo — nunca
        dados nem valores de parâmetros."""
        c = await authorize(request, name, "consulta SQL", ("query",))
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
            return await c.fail(
                "consulta SQL", "corpo inválido", 400, 'Corpo: {"sql", "params", "max_rows"}.'
            )
        shown = f"sql {sql_hash(sql)} ({len(sql)} caracteres, {len(params)} parâmetros)"
        if not isinstance(c.backend, SqlBackend):
            return await c.fail("consulta SQL", shown, 400, "Esta conexão não é de banco de dados.")
        try:
            res = await c.backend.query(sql, params, max_rows)
        except SqlError as e:
            return await c.fail("consulta SQL", shown, e.status, e.message)
        c.release()
        cut = " · truncada no limite" if res.truncated else ""
        await c.record(
            "Consulta SQL", f"{c.conn['name']} · {shown} · {len(res.rows)} linhas{cut} · {res.ms} ms"
        )
        return JSONResponse(res.json(), headers={"cache-control": "no-store"})

    @app.exception_handler(404)
    async def not_found(request: Request, exc: Any) -> Response:
        return fail(404, "Rota desconhecida.")

    return app


async def serve(app: FastAPI, addr: str) -> tuple[Any, asyncio.Task[None]]:
    """Sobe o broker em `host:porta` (uvicorn no mesmo laço de eventos)."""
    import uvicorn

    host, _, port = addr.rpartition(":")
    server = uvicorn.Server(uvicorn.Config(app, host=host or "0.0.0.0", port=int(port), log_level="warning"))  # noqa: S104
    task = asyncio.create_task(server.serve())
    return server, task


async def stop(server: Any, task: asyncio.Task[None]) -> None:
    server.should_exit = True
    with contextlib.suppress(Exception):
        await asyncio.wait_for(task, 5)
