"""Internal MCP of the app (wave 11): tools **generated from the app's OpenAPI** at
``POST /{app}/_hub/mcp`` (JSON-RPC 2.0, MCP 2025-06-18) — only for the hub, over the internal
network, with ``x-hub-internal-token`` (the gateway refuses the browser's ``/_hub/*``).

Each operation under ``/{app}/api/`` becomes a
tool (``operationId``), with input = parameters + ``body``; ``tools/call`` runs the
operation **inside the app itself** (in-process ASGI call, same middlewares),
passing on the identity the hub sent. ``HUB_MCP_ENABLED=0`` turns it off (404).
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
TOKEN_HEADER = "x-hub-internal-token"  # noqa: S105 — header name
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
    """Wave 11 (I3): read only GET/HEAD or ``x-hub-readonly: true``."""
    return m in ("get", "head") or op.get("x-hub-readonly") is True


def _destructive(m: str, op: dict[str, Any]) -> bool:
    """Every mutation (POST/PUT/PATCH/DELETE) is destructive by default — the hub asks for
    approval; ``x-hub-destructive: false`` does not waive it. It is only the app's hint: the hub
    classifies by the package's ``openapi.json`` and by the reviewed list ``mcp.confirm_exempt``."""
    if op.get("x-hub-destructive") is True:
        return True
    return not _read_only(m, op)


def _tool_name(s: str) -> str:
    n = re.sub(r"[^a-z0-9_-]", "_", s.lower())[:64]
    return n.strip("_")


def _unique_name(base: str, out: list[ToolDef]) -> str:
    name, i = base, 2
    while any(t.name == name for t in out):
        name, i = f"{base}_{i}", i + 1
    return name


@dataclass
class _Params:
    """Input schema pieces of an operation: properties, required names, path and query parameters."""

    props: dict[str, Any] = field(default_factory=dict)
    required: list[str] = field(default_factory=list)
    path: list[str] = field(default_factory=list)
    query: list[str] = field(default_factory=list)

    def add(self, p: dict[str, Any]) -> None:
        pn, loc = p.get("name"), p.get("in")
        if not isinstance(pn, str) or loc not in ("path", "query"):
            return
        s = dict(p.get("schema") or {"type": "string"})
        if p.get("description"):
            s["description"] = p["description"]
        self.props[pn] = s
        if loc == "path":
            self.path.append(pn)
            self.required.append(pn)
            return
        self.query.append(pn)
        if p.get("required"):
            self.required.append(pn)


def _tool_of(doc: dict[str, Any], path: str, m: str, op: dict[str, Any], name: str) -> ToolDef:
    ps = _Params()
    for p in op.get("parameters") or []:
        ps.add(p)
    body = (((op.get("requestBody") or {}).get("content") or {}).get("application/json") or {}).get("schema")
    if isinstance(body, dict):
        ps.props["body"] = _inline_refs(body, doc)
        if (op.get("requestBody") or {}).get("required"):
            ps.required.append("body")
    summary = op.get("summary") or name
    return ToolDef(
        name=name,
        title=summary,
        description=f"{summary} ({m.upper()} {path})",
        input_schema={"type": "object", "properties": ps.props, "required": ps.required},
        method=m.upper(),
        path=path,
        path_params=ps.path,
        query_params=ps.query,
        has_body=isinstance(body, dict),
        destructive=_destructive(m, op),
        read_only=_read_only(m, op),
    )


def _operations(doc: dict[str, Any], app_id: str) -> list[tuple[str, str, dict[str, Any]]]:
    """``(path, method, operation)`` under ``/{app_id}/api/`` that the MCP exposes."""
    prefix = f"/{app_id}/api/"
    out: list[tuple[str, str, dict[str, Any]]] = []
    for path, item in (doc.get("paths") or {}).items():
        if not path.startswith(prefix) or not isinstance(item, dict):
            continue
        for m, op in item.items():
            if m in METHODS and isinstance(op, dict) and op.get("x-hub-mcp") is not False:
                out.append((path, m, op))
    return out


def tools_from_openapi(doc: dict[str, Any], app_id: str) -> list[ToolDef]:
    out: list[ToolDef] = []
    for path, m, op in _operations(doc, app_id):
        base = _tool_name(op.get("operationId") or f"{m}_{path}") or _tool_name(f"{m}_{path}")
        out.append(_tool_of(doc, path, m, op, _unique_name(base, out)))
    return out


def _inline_refs(schema: Any, doc: dict[str, Any], depth: int = 0) -> Any:
    """Resolve ``$ref`` locais (``#/components/schemas/X``) — o cliente MCP recebe o
    esquema autocontido."""
    if isinstance(schema, dict):
        ref = schema.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/"):
            if depth > 8:  # only nested $ref count: cuts cycles without mangling deep schemas
                return {}
            node: Any = doc
            for part in ref[2:].split("/"):
                node = node.get(part, {}) if isinstance(node, dict) else {}
            return _inline_refs(node, doc, depth + 1)
        return {k: _inline_refs(v, doc, depth) for k, v in schema.items()}
    if isinstance(schema, list):
        return [_inline_refs(v, doc, depth) for v in schema]
    return schema


async def asgi_call(
    app: Any, method: str, path: str, query: str, headers: list[tuple[bytes, bytes]], body: bytes
) -> tuple[int, bytes]:
    """HTTP request in-process against an ASGI application (no network, no httpx)."""
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


def _find_tool(req: Request, tools: list[ToolDef], name: Any) -> ToolDef:
    t = next((x for x in tools if x.name == name), None)
    if t is None:
        raise _RpcError(-32602, f"ferramenta desconhecida: {name}")
    # Wave 11 (N7): the hub sends the operation it classified (from the package's openapi.json);
    # if the name points to another operation in the running app, the call is refused.
    want = req.headers.get("x-hub-mcp-op")
    if want is not None and want != f"{t.method} {t.path}":
        raise _RpcError(
            -32602,
            f"operação divergente do pacote: {name} é {t.method} {t.path}, o hub esperava {want}",
        )
    return t


def _request_target(t: ToolDef, args: dict[str, Any]) -> tuple[str, str]:
    """Path (with its parameters filled in) and query string of the call."""
    path = t.path
    for p in t.path_params:
        if args.get(p) is None:
            raise _RpcError(-32602, f"argumento obrigatório ausente: {p}")
        path = path.replace("{" + p + "}", quote(_arg_text(args[p]), safe=""))
    query = "&".join(
        f"{q}={quote(_arg_text(args[q]), safe='')}" for q in t.query_params if args.get(q) is not None
    )
    return path, query


async def _call_tool(req: Request, params: dict[str, Any], tools: list[ToolDef], app: Any) -> dict[str, Any]:
    name = params.get("name")
    t = _find_tool(req, tools, name)
    args = params.get("arguments") or {}
    if not isinstance(args, dict):
        raise _RpcError(-32602, "arguments deve ser um objeto")
    path, query = _request_target(t, args)
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


def _tools_list(tools: list[ToolDef]) -> dict[str, Any]:
    return {
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
            for t in tools
        ]
    }


def _rpc_error(mid: Any, code: int, message: str) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}})


def _denied(token: str | None, req: Request) -> JSONResponse | None:
    """Answer when the internal token is missing or wrong (``None`` = allowed)."""
    if token is None:
        return JSONResponse({"error": "HUB_INTERNAL_TOKEN não configurado"}, status_code=403)
    if not ct_eq(req.headers.get(TOKEN_HEADER, "").encode("latin-1", "replace"), token.encode()):
        return JSONResponse({"error": "token interno inválido"}, status_code=401)
    return None


@dataclass
class _Server:
    app_id: str
    app: Callable[[], Any]
    doc: Callable[[], dict[str, Any]]
    version: str
    _tools: list[ToolDef] | None = None

    def tools(self) -> list[ToolDef]:
        # Resolved on the first call: the OpenAPI only exists once the app is fully built.
        if self._tools is None:
            self._tools = tools_from_openapi(self.doc(), self.app_id)
        return self._tools

    async def dispatch(self, req: Request, method: Any, params: dict[str, Any]) -> dict[str, Any]:
        if method == "initialize":
            pv = params.get("protocolVersion")
            return {
                "protocolVersion": pv if pv in SUPPORTED else PROTOCOL,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": f"{self.app_id}-app", "version": self.version},
            }
        if method == "ping":
            return {}
        if method == "tools/list":
            return _tools_list(self.tools())
        if method == "tools/call":
            return await _call_tool(req, params, self.tools(), self.app)
        raise _RpcError(-32601, f"método não suportado: {method}")

    async def handle(self, req: Request) -> Response:
        try:
            msg = json.loads(await req.body())
        except ValueError:
            return _rpc_error(None, -32700, "JSON inválido")
        if not isinstance(msg, dict):
            return _rpc_error(None, -32600, "lotes não suportados")
        if "id" not in msg:
            return Response(status_code=202)
        mid = msg["id"]
        try:
            result = await self.dispatch(req, msg.get("method"), msg.get("params") or {})
        except _RpcError as e:
            return _rpc_error(mid, e.code, e.message)
        return JSONResponse({"jsonrpc": "2.0", "id": mid, "result": result})


def router(
    app_id: str,
    token: str | None,
    app: Callable[[], Any],
    doc: Callable[[], dict[str, Any]],
    version: str = "1",
) -> APIRouter:
    """``POST /{app_id}/_hub/mcp``. ``app``/``doc``: o app ASGI completo e o OpenAPI
    (resolved on the first call)."""
    r = APIRouter(include_in_schema=False)
    enabled = os.environ.get("HUB_MCP_ENABLED", "1").strip() != "0"
    server = _Server(app_id, app, doc, version)

    @r.post(f"/{app_id}/_hub/mcp")
    async def mcp(req: Request) -> Response:
        if not enabled:
            return JSONResponse({"error": "MCP desligado"}, status_code=404)
        return _denied(token, req) or await server.handle(req)

    @r.get(f"/{app_id}/_hub/mcp")
    async def mcp_get() -> Response:
        return Response(status_code=405)

    return r


class _RpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
