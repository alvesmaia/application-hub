"""Execution of the public `/api/v1` API: authorization, in-process facade over the internal routes, paginated lists
(envelope), rate limit, audit, RFC 9457 errors and the HTTP routes. Also the engine behind the MCP tools."""

from __future__ import annotations

import asyncio
import json
from typing import Any, cast
from urllib.parse import parse_qsl, quote

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from hub.api import ops as opsmod
from hub.api.ops import MULTIPART, Op
from hub.api.v1_authz import authorize as authorize
from hub.api.v1_authz import authorize_scope as authorize_scope
from hub.api.v1_base import _SLUGS, Args, P, Problem, audit, take_rate
from hub.api.v1_base import principal as principal
from hub.api.v1_native import native

# ---------------------------------------------------------------- in-process facade


def _target_uri(op: Op, args: Args) -> tuple[str, str]:
    if op.target is None:
        raise Problem(500, "error", "operação sem rota interna")
    method, tmpl = op.target
    uri = tmpl
    for k in {s.strip("{}") for s in tmpl.split("/") if s.startswith("{")}:
        v = args.path.get(k, "")
        if not _safe_seg(v):
            raise Problem(400, "bad-request", f"Parâmetro {k} inválido.")
        uri = uri.replace("{" + k + "}", v)
    return method, uri


def _request_payload(
    request: Request, op: Op, method: str, args: Args
) -> tuple[list[tuple[bytes, bytes]], bytes]:
    headers: list[tuple[bytes, bytes]] = [(b"host", request.headers.get("host", "hub").encode())]
    body = b""
    if op.body == MULTIPART and args.body is not None:
        headers += [(b"content-type", (args.content_type or "").encode()), (b"x-hub-request", b"1")]
        body = args.body
    elif args.body is not None:
        headers.append((b"content-type", b"application/json"))
        body = args.body
    elif method != "GET":
        headers.append((b"content-type", b"application/json"))
        body = b"{}"
    for h in ("accept-language", "user-agent", "x-forwarded-for"):
        if v := request.headers.get(h):
            headers.append((h.encode(), v.encode()))
    return headers, body


async def _asgi_call(request: Request, scope: dict[str, Any], body: bytes) -> tuple[int, bytes]:
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if sent:
            await asyncio.sleep(3600)
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    status: int = 500
    chunks: list[bytes] = []

    async def send(msg: dict[str, Any]) -> None:
        nonlocal status
        if msg["type"] == "http.response.start":
            status = cast(int, msg["status"])
        elif msg["type"] == "http.response.body":
            chunks.append(msg.get("body", b""))

    await request.app(scope, receive, send)
    return status, b"".join(chunks)


def _decoded(status: int, raw: bytes) -> Any:
    try:
        v: Any = json.loads(raw) if raw else None
    except ValueError:
        v = {"text": raw.decode("utf-8", "replace")}
    if not 200 <= status < 300:
        msg = None
        if isinstance(v, dict):
            msg = v.get("error") or v.get("detail") or v.get("message")
        if isinstance(msg, list):
            msg = "; ".join(str(x.get("msg", x)) if isinstance(x, dict) else str(x) for x in msg)
        raise Problem(status, _SLUGS.get(status, "error"), str(msg or f"erro {status}"))
    return v


async def internal(request: Request, p: P, op: Op, args: Args) -> tuple[int, Any]:
    """Calls the internal route (same handlers as the UI) with `p`'s identity, without going through the network."""
    status, raw = await internal_raw(request, p, op, args)
    return status, _decoded(status, raw)


async def internal_raw(request: Request, p: P, op: Op, args: Args) -> tuple[int, bytes]:
    """`internal` without decoding: the internal route's status and body as they came (errors included)."""
    method, uri = _target_uri(op, args)
    qs = [f"{k}={quote(v, safe='')}" for k in op.passed if (v := args.q(k)) is not None]
    if op.id == "audit_query":
        qs.append("limit=5000")
    headers, body = _request_payload(request, op, method, args)
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "scheme": "http",
        "path": uri, "raw_path": uri.encode(), "root_path": "", "query_string": "&".join(qs).encode(),
        "headers": headers, "server": ("hub", 80), "client": request.client and (request.client.host, 0),
        "app": request.app, "state": {}, "hub_facade": (p.user, p.token),
    }  # fmt: skip
    return await _asgi_call(request, scope, body)


def _safe_seg(v: str) -> bool:
    return (
        0 < len(v) <= 80
        and all(c.isascii() and (c.isalnum() or c in "._-:") for c in v)
        and v not in (".", "..")
    )


# ---------------------------------------------------------------- listas


def _matches(it: Any, f: str, want: str) -> bool:
    v = it.get(f) if isinstance(it, dict) else None
    if isinstance(v, bool):
        return str(v).lower() == want
    if isinstance(v, str):
        return bool(v.lower() == want.lower())
    return bool(isinstance(v, int | float) and str(v) == want)


def _filtered(op: Op, p: P, args: Args, items: list[Any]) -> list[Any]:
    if apps := p.token_apps():
        items = [
            i
            for i in items
            if not isinstance(i, dict) or not isinstance(i.get("app"), str) or i["app"] in apps
        ]
    for f in op.filters:
        if want := args.q(f):
            items = [i for i in items if _matches(i, f, want)]
    if "q" not in op.passed and (q := (args.q("q") or "").strip().lower()):
        items = [i for i in items if q in json.dumps(i, ensure_ascii=False).lower()]
    return items


def _num(args: Args, k: str) -> int | None:
    v = args.q(k)
    if v is None:
        return None
    if not v.isdigit():
        raise Problem(400, "bad-request", f"{k} inválido.")
    return int(v)


def _next_link(op: Op, args: Args, limit: int, offset: int) -> str:
    q = [f"{k}={quote(v, safe='')}" for k, v in args.query if k not in ("offset", "limit")]
    q += [f"limit={limit}", f"offset={offset}"]
    path = op.path
    for k, v in args.path.items():
        path = path.replace("{" + k + "}", v)
    return f"/api/v1{path}?{'&'.join(q)}"


def paginate(op: Op, p: P, args: Args, value: Any, cfg: Any) -> Any:
    items = value if not op.list else (value or {}).get(op.list, []) if isinstance(value, dict) else []
    if not isinstance(items, list):
        return items
    items = _filtered(op, p, args, items)
    total = len(items)
    limit = min(max(_num(args, "limit") or cfg.api.page_size, 1), cfg.api.max_page_size)
    offset = min(_num(args, "offset") or 0, total)
    page = items[offset : offset + limit]
    end = offset + len(page)
    nxt = _next_link(op, args, limit, end) if end < total else None
    return {"items": page, "total": total, "limit": limit, "offset": offset, "next": nxt}


# ---------------------------------------------------------------- chamada


async def call(request: Request, op: Op, p: P, args: Args) -> tuple[int, Any]:
    if e := authorize(op, p, args):
        raise e
    if e := await authorize_scope(request, op, p, args):
        raise e
    for prm in op.params:
        if prm.required:
            ok = prm.name in args.path if prm.loc == "path" else bool(args.q(prm.name))
            if not ok:
                raise Problem(400, "bad-request", f"Parâmetro obrigatório ausente: {prm.name}.")
    if op.native:
        status, value = 200, await native(request, op, p, args)
    else:
        status, value = await internal(request, p, op, args)
    if op.list is not None:
        value = paginate(op, p, args, value, request.app.state.apicfg)
    return status, value


# ---------------------------------------------------------------- HTTP

_DOCS = """<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>API do Application Hub · v1</title>
<link rel="stylesheet" href="/assets/api-docs.css">
</head><body>
<header class="apidoc-head"><h1>API do Application Hub <small>v1</small></h1>
<p>Documento OpenAPI 3.1: <a href="/api/v1/openapi.json">/api/v1/openapi.json</a>. Autentique com a sua sessão ou com um token de API
(<a href="/admin/tokens">Acesso › Credenciais</a>, escopos <code>api:read</code>/<code>api:write</code>) em <code>Authorization: Bearer …</code>.
Clientes de IA: servidor MCP em <code>/mcp</code> (escopo <code>mcp</code>).</p></header>
<main id="apidoc" aria-live="polite"><p>Carregando…</p></main>
<script src="/assets/api-docs.js" defer></script>
</body></html>"""


async def docs(request: Request) -> Response:
    cfg = request.app.state.apicfg
    if not cfg.api.enabled or not cfg.api.docs:
        return Problem(404, "not-found", "Documentação desligada ([api.docs].enabled = false).").response(
            request.url.path
        )
    return HTMLResponse(_DOCS, headers={"cache-control": "no-store"})


async def _v1_gate(request: Request, rest: str) -> Response | tuple[P, Op, dict[str, str]]:
    """The caller and the operation of a `/api/v1` request, or the answer that ends it. Raises `Problem`."""
    cfg = request.app.state.apicfg
    if not cfg.api.enabled:
        raise Problem(
            404, "not-found", "A API pública está desligada nesta instalação ([api].enabled = false)."
        )
    p = principal(request)
    if p is None:
        raise Problem(401, "unauthorized", "Entre ou use um token de API.")
    if request.url.path == "/api/v1/openapi.json":
        if not cfg.api.docs:
            raise Problem(404, "not-found", "Documentação desligada ([api.docs].enabled = false).")
        return JSONResponse(opsmod.openapi(), headers={"cache-control": "no-store"})
    if (wait := await take_rate(request.app, "api", p.rate_key, cfg.api.rate_per_min)) is not None:
        raise Problem(
            429, "rate-limited", f"Limite de {cfg.api.rate_per_min} requisições por minuto atingido.", wait
        )
    found = opsmod.route(request.method, "/" + rest)
    if found is True:
        raise Problem(405, "method-not-allowed", "Método não suportado neste recurso.")
    if found is False:
        raise Problem(404, "not-found", "Recurso inexistente na API v1.")
    op, params = found
    return p, op, params


async def _v1_body(request: Request, op: Op, ct: str | None) -> bytes:
    limit = 21 * 1024 * 1024 if op.body == MULTIPART else 256 * 1024
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            raise Problem(413, "too-large", "Corpo grande demais.")
    if op.write and op.body and op.body != MULTIPART:
        if (ct or "").split(";")[0].strip().lower() != "application/json":
            raise Problem(415, "unsupported-media-type", "Envie application/json.")
        try:
            json.loads(body)
        except ValueError:
            raise Problem(400, "bad-request", "JSON inválido.") from None
    return body


async def v1(request: Request, rest: str) -> Response:
    path = request.url.path
    try:
        gate = await _v1_gate(request, rest)
        if isinstance(gate, Response):
            return gate
        p, op, params = gate
        ct = request.headers.get("content-type")
        body = await _v1_body(request, op, ct)
    except Problem as e:
        return e.response(path)
    args = Args(params, parse_qsl(request.url.query, keep_blank_values=True), body or None, ct)
    status, value, err = 200, None, None
    try:
        status, value = await call(request, op, p, args)
    except Problem as e:
        err = e
    if op.write:
        await audit(request, p, f"API v1: {op.id}", f"{op.method} {path} → {err.status if err else status}",
                    params.get("app", "platform"))  # fmt: skip
    if err:
        return err.response(path)
    return JSONResponse(value, status, headers={"cache-control": "no-store"})


def mount_v1(app: FastAPI) -> None:
    app.add_api_route("/api/docs", docs, methods=["GET"], include_in_schema=False)
    app.add_api_route("/api/docs/", docs, methods=["GET"], include_in_schema=False)
    app.add_api_route("/api/v1/{rest:path}", v1, methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                      include_in_schema=False)  # fmt: skip
