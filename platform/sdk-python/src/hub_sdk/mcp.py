"""MCP interno do app (onda 11): ferramentas **geradas do OpenAPI** do app em
``POST /{app}/_hub/mcp`` (JSON-RPC 2.0, MCP 2025-06-18) — só para o hub, pela rede
interna, com ``x-hub-internal-token`` (o gateway recusa ``/_hub/*`` do navegador).

Igual ao SDK Rust (``hub_sdk::mcp``): cada operação sob ``/{app}/api/`` vira uma
ferramenta (``operationId``), com entrada = parâmetros + ``body``; ``tools/call`` executa a
operação **dentro do próprio app** (chamada ASGI em processo, mesmos middlewares),
repassando a identidade que o hub mandou. ``HUB_MCP_ENABLED=0`` desliga (404).
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from hub_sdk.schema import ct_eq

PROTOCOL = "2025-06-18"
SUPPORTED = ("2025-11-25", "2025-06-18", "2025-03-26")
MAX_RESULT = 256 * 1024
TOKEN_HEADER = "x-hub-internal-token"  # noqa: S105 — nome do cabeçalho
FORWARD = ("x-hub-user", "x-hub-user-sig", "x-hub-locale", "x-hub-flags", "traceparent")
METHODS = ("get", "head", "post", "put", "patch", "delete")


@dataclass
class ToolDef:
    name: str
    title: str
    description: str
    input_schema: dict[str, Any]
    method: str
    path: str
    path_params: list[str] = field(default_factory=list)
    query_params: list[str] = field(default_factory=list)
    has_body: bool = False
    destructive: bool = False
    read_only: bool = False


def _read_only(m: str, op: dict[str, Any]) -> bool:
    """Onda 11 (I3): leitura só GET/HEAD ou ``x-hub-readonly: true``."""
    return m in ("get", "head") or op.get("x-hub-readonly") is True


def _destructive(m: str, op: dict[str, Any]) -> bool:
    """Toda mutação (POST/PUT/PATCH/DELETE) é destrutiva por padrão — o hub pede
    aprovação; ``x-hub-destructive: false`` não dispensa. É só a dica do app: o hub
    classifica pelo ``openapi.json`` do pacote e pela lista revisada ``mcp.confirm_exempt``."""
    if op.get("x-hub-destructive") is True:
        return True
    return not _read_only(m, op)


def _tool_name(s: str) -> str:
    n = re.sub(r"[^a-z0-9_-]", "_", s.lower())[:64]
    return n.strip("_")


def tools_from_openapi(doc: dict[str, Any], app_id: str) -> list[ToolDef]:
    prefix = f"/{app_id}/api/"
    out: list[ToolDef] = []
    for path, item in (doc.get("paths") or {}).items():
        if not path.startswith(prefix) or not isinstance(item, dict):
            continue
        for m, op in item.items():
            if m not in METHODS or not isinstance(op, dict) or op.get("x-hub-mcp") is False:
                continue
            base = _tool_name(op.get("operationId") or f"{m}_{path}") or _tool_name(f"{m}_{path}")
            name, i = base, 2
            while any(t.name == name for t in out):
                name, i = f"{base}_{i}", i + 1
            props: dict[str, Any] = {}
            required: list[str] = []
            pp: list[str] = []
            qp: list[str] = []
            for p in op.get("parameters") or []:
                pn, loc = p.get("name"), p.get("in")
                if not isinstance(pn, str) or loc not in ("path", "query"):
                    continue
                s = dict(p.get("schema") or {"type": "string"})
                if p.get("description"):
                    s["description"] = p["description"]
                props[pn] = s
                if loc == "path":
                    pp.append(pn)
                    required.append(pn)
                else:
                    qp.append(pn)
                    if p.get("required"):
                        required.append(pn)
            body = (((op.get("requestBody") or {}).get("content") or {}).get("application/json") or {}).get(
                "schema"
            )
            if isinstance(body, dict):
                props["body"] = _inline_refs(body, doc)
                if (op.get("requestBody") or {}).get("required"):
                    required.append("body")
            summary = op.get("summary") or name
            out.append(
                ToolDef(
                    name=name,
                    title=summary,
                    description=f"{summary} ({m.upper()} {path})",
                    input_schema={"type": "object", "properties": props, "required": required},
                    method=m.upper(),
                    path=path,
                    path_params=pp,
                    query_params=qp,
                    has_body=isinstance(body, dict),
                    destructive=_destructive(m, op),
                    read_only=_read_only(m, op),
                )
            )
    return out


def _inline_refs(schema: Any, doc: dict[str, Any], depth: int = 0) -> Any:
    """Resolve ``$ref`` locais (``#/components/schemas/X``) — o cliente MCP recebe o
    esquema autocontido."""
    if depth > 8:
        return {}
    if isinstance(schema, dict):
        ref = schema.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/"):
            node: Any = doc
            for part in ref[2:].split("/"):
                node = node.get(part, {}) if isinstance(node, dict) else {}
            return _inline_refs(node, doc, depth + 1)
        return {k: _inline_refs(v, doc, depth + 1) for k, v in schema.items()}
    if isinstance(schema, list):
        return [_inline_refs(v, doc, depth + 1) for v in schema]
    return schema


async def asgi_call(
    app: Any, method: str, path: str, query: str, headers: list[tuple[bytes, bytes]], body: bytes
) -> tuple[int, bytes]:
    """Requisição HTTP em processo contra uma aplicação ASGI (sem rede, sem httpx)."""
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query.encode(),
        "root_path": "",
        "headers": [*headers, (b"content-length", str(len(body)).encode())],
        "client": ("127.0.0.1", 0),
        "server": ("hub-mcp", 80),
    }
    sent = False
    status = 500
    chunks: list[bytes] = []

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(msg: dict[str, Any]) -> None:
        nonlocal status
        if msg["type"] == "http.response.start":
            status = int(msg["status"])
        elif msg["type"] == "http.response.body" and sum(map(len, chunks)) < 4 * MAX_RESULT:
            chunks.append(msg.get("body", b""))

    await app(scope, receive, send)
    return status, b"".join(chunks)


def _result(status: int, body: bytes) -> dict[str, Any]:
    try:
        v: Any = json.loads(body)
    except ValueError:
        v = {"text": body.decode("utf-8", "replace")}
    text = json.dumps(v, ensure_ascii=False, indent=2)
    truncated = len(text) > MAX_RESULT
    if truncated:
        text = text[:MAX_RESULT] + "\n… (truncado)"
    err = not 200 <= status < 300
    r: dict[str, Any] = {"content": [{"type": "text", "text": text}], "isError": err}
    if not err and not truncated and isinstance(v, dict):
        r["structuredContent"] = v
    return r


def _arg_text(v: Any) -> str:
    return v if isinstance(v, str) else json.dumps(v)


def router(
    app_id: str,
    token: str | None,
    app: Callable[[], Any],
    doc: Callable[[], dict[str, Any]],
    version: str = "1",
) -> APIRouter:
    """``POST /{app_id}/_hub/mcp``. ``app``/``doc``: o app ASGI completo e o OpenAPI
    (resolvidos na primeira chamada)."""
    r = APIRouter(include_in_schema=False)
    enabled = os.environ.get("HUB_MCP_ENABLED", "1").strip() != "0"
    cache: dict[str, list[ToolDef]] = {}

    def tools() -> list[ToolDef]:
        if "t" not in cache:
            cache["t"] = tools_from_openapi(doc(), app_id)
        return cache["t"]

    async def call(req: Request, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        t = next((x for x in tools() if x.name == name), None)
        if t is None:
            raise _RpcError(-32602, f"ferramenta desconhecida: {name}")
        # Onda 11 (N7): o hub manda a operação que classificou (do openapi.json do pacote);
        # se o nome aponta para outra operação no app no ar, a chamada é recusada.
        want = req.headers.get("x-hub-mcp-op")
        if want is not None and want != f"{t.method} {t.path}":
            raise _RpcError(
                -32602,
                f"operação divergente do pacote: {name} é {t.method} {t.path}, o hub esperava {want}",
            )
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            raise _RpcError(-32602, "arguments deve ser um objeto")
        path = t.path
        for p in t.path_params:
            if args.get(p) is None:
                raise _RpcError(-32602, f"argumento obrigatório ausente: {p}")
            path = path.replace("{" + p + "}", quote(_arg_text(args[p]), safe=""))
        query = "&".join(
            f"{q}={quote(_arg_text(args[q]), safe='')}" for q in t.query_params if args.get(q) is not None
        )
        headers = [(b"x-hub-mcp", b"1")]
        for h in FORWARD:
            v = req.headers.get(h)
            if v is not None:
                headers.append((h.encode(), v.encode("latin-1", "replace")))
        body = b""
        if t.has_body:
            headers.append((b"content-type", b"application/json"))
            body = json.dumps(args.get("body") or {}).encode()
        status, out = await asgi_call(app(), t.method, path, query, headers, body)
        return _result(status, out)

    @r.post(f"/{app_id}/_hub/mcp")
    async def mcp(req: Request) -> Response:
        if not enabled:
            return JSONResponse({"error": "MCP desligado"}, status_code=404)
        if token is None:
            return JSONResponse({"error": "HUB_INTERNAL_TOKEN não configurado"}, status_code=403)
        if not ct_eq(req.headers.get(TOKEN_HEADER, "").encode("latin-1", "replace"), token.encode()):
            return JSONResponse({"error": "token interno inválido"}, status_code=401)
        try:
            msg = json.loads(await req.body())
        except ValueError:
            return JSONResponse(
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "JSON inválido"}}
            )
        if not isinstance(msg, dict):
            return JSONResponse(
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "lotes não suportados"}}
            )
        if "id" not in msg:
            return Response(status_code=202)
        mid, method = msg["id"], msg.get("method")
        params = msg.get("params") or {}
        try:
            if method == "initialize":
                pv = params.get("protocolVersion")
                result: dict[str, Any] = {
                    "protocolVersion": pv if pv in SUPPORTED else PROTOCOL,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": f"{app_id}-app", "version": version},
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {
                    "tools": [
                        {
                            "name": t.name,
                            "title": t.title,
                            "description": t.description,
                            "inputSchema": t.input_schema,
                            "annotations": {
                                "readOnlyHint": t.read_only,
                                "destructiveHint": t.destructive,
                                "openWorldHint": False,
                            },
                        }
                        for t in tools()
                    ]
                }
            elif method == "tools/call":
                result = await call(req, params)
            else:
                raise _RpcError(-32601, f"método não suportado: {method}")
        except _RpcError as e:
            return JSONResponse(
                {"jsonrpc": "2.0", "id": mid, "error": {"code": e.code, "message": e.message}}
            )
        return JSONResponse({"jsonrpc": "2.0", "id": mid, "result": result})

    @r.get(f"/{app_id}/_hub/mcp")
    async def mcp_get() -> Response:
        return Response(status_code=405)

    return r


class _RpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
