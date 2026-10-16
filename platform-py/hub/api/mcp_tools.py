"""MCP tools and resources of the hub: tool list and arguments (from the `/api/v1` table and the apps), sessions,
destructive-action confirmations and `tools/call`. The JSON-RPC transport lives in `hub.api.mcp`."""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import time
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import Response

from hub.api import ops as opsmod
from hub.api import schemas_build as sc
from hub.api.mcp_apps import app_tools, run_app
from hub.api.ops import MULTIPART, Op
from hub.api.v1 import call
from hub.api.v1_authz import authorize, authorize_scope
from hub.api.v1_base import Args, P, Problem, audit, take_rate

PROTOCOL = "2025-11-25"
MODERN = "2026-07-28"
LEGACY = ("2025-11-25", "2025-06-18", "2025-03-26")
SUPPORTED = (MODERN, *LEGACY)
SESSION, VERSION = "mcp-session-id", "mcp-protocol-version"
META_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CLIENT = "io.modelcontextprotocol/clientInfo"
PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS = -32700, -32600, -32601, -32602
HEADER_MISMATCH, UNSUPPORTED_VERSION = -32020, -32022
SESSION_TTL_MS = 8 * 3600 * 1000
KEEPALIVE, GET_KEEPALIVE, GET_MAX = 10, 15, 30 * 60
K_SESSION, K_CONFIRM = "mcp.session", "mcp.confirm"
RESOURCES = (
    ("hub://apps", "apps", "Catálogo de apps", "apps_list"),
    ("hub://status", "status", "Status page (situação dos apps)", "status_get"),
)
CONFIRM_DESC = "Token devolvido pela 1ª chamada; reenvie só depois de o usuário confirmar a ação."


class Rpc(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def rpc_error(id_: Any, code: int, msg: str, data: Any = None) -> dict[str, Any]:
    e: dict[str, Any] = {"code": code, "message": msg}
    if data is not None:
        e["data"] = data
    return {"jsonrpc": "2.0", "id": id_, "error": e}


def json_response(status: int, v: Any, headers: dict[str, str] | None = None) -> Response:
    return Response(json.dumps(v, ensure_ascii=False), status, headers={"cache-control": "no-store", **(headers or {})},
                    media_type="application/json")  # fmt: skip


def plain(status: int, msg: str, headers: dict[str, str] | None = None) -> Response:
    return json_response(status, {"error": msg}, headers)


def text_result(app: FastAPI, v: Any, is_error: bool = False) -> dict[str, Any]:
    cap = app.state.apicfg.mcp.max_result_kb * 1024
    text = json.dumps(v, ensure_ascii=False, indent=2)
    raw = text.encode()
    truncated = len(raw) > cap
    if truncated:
        text = raw[:cap].decode("utf-8", "ignore") + "\n… (resultado truncado: use limit/offset ou filtros)"
    r: dict[str, Any] = {"content": [{"type": "text", "text": text}], "isError": is_error}
    if isinstance(v, dict) and not truncated and not is_error:
        r["structuredContent"] = v
    return r


def client_label(params: dict[str, Any]) -> str:
    def clean(v: Any, n: int) -> str:
        return "".join(c for c in str(v or "") if c.isprintable())[:n]

    info = params.get("clientInfo") or {}
    name, ver = clean(info.get("name"), 60), clean(info.get("version"), 30)
    return "" if not name else f"{name} {ver}" if ver else name


def args_hash(args: dict[str, Any]) -> str:
    a = {k: v for k, v in args.items() if k != "confirm_token"}
    return sha(json.dumps(a, sort_keys=True, ensure_ascii=False, separators=(",", ":")))


# ---------------------------------------------------------------- ferramentas


def hub_input_schema(op: Op, confirm: bool) -> dict[str, Any]:
    props: dict[str, Any] = {}
    required: list[str] = []
    for p in op.params:
        props[p.name] = p.schema | {"description": p.desc}
        if p.required or p.loc == "path":
            required.append(p.name)
    if op.list is not None:
        props["limit"] = {"type": "integer", "minimum": 1, "description": "Itens (padrão 20)"}
        props["offset"] = {"type": "integer", "minimum": 0}
        if "q" not in op.passed:
            props["q"] = {"type": "string", "description": "Texto em qualquer campo"}
        for f in op.filters:
            props[f] = {"type": "string", "description": f"Filtra por {f}"}
    if op.body and op.body != MULTIPART:
        props["body"] = sc.inline(op.body)
        required.append("body")
    if confirm:
        props["confirm_token"] = {"type": "string", "description": CONFIRM_DESC}
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


def destructive_note(mode: str) -> str:
    return ". AÇÃO DESTRUTIVA: exige confirmação do usuário (confirm_token)."


def hub_tools(app: FastAPI, p: P) -> list[dict[str, Any]]:
    cfg = app.state.apicfg
    if not cfg.api.enabled:
        return []
    mode, out = cfg.mcp.destructive_actions, []
    for op in opsmod.OPS:
        if not op.mcp or authorize(op, p) or (op.destructive and mode == "deny"):
            continue
        needs = op.destructive and mode == "confirm"
        out.append({
            "name": op.id, "title": op.summary,
            "description": f"{op.summary} ({op.method} /api/v1{op.path})" + (destructive_note(mode) if op.destructive else ""),
            "inputSchema": hub_input_schema(op, needs),
            "annotations": {"title": op.summary, "readOnlyHint": not op.write, "destructiveHint": op.destructive,
                            "idempotentHint": not op.write, "openWorldHint": False},
        })  # fmt: skip
    return out


def hub_args(app: FastAPI, op: Op, args: dict[str, Any]) -> Args:
    cfg = app.state.apicfg
    a = Args()
    for prm in op.params:
        v = args.get(prm.name)
        if v is not None:
            s = v if isinstance(v, str) else json.dumps(v)
            if prm.loc == "path":
                a.path[prm.name] = s
            else:
                a.query.append((prm.name, s))
    if op.list is not None:
        cap = cfg.mcp.logs_tail_max if op.id == "logs_tail" else cfg.api.max_page_size
        lim = args.get("limit")
        limit = min(max(lim if isinstance(lim, int) else 20, 1), cap)
        a.query.append(("limit", str(limit)))
        for k in ("offset", "q", *op.filters):
            v = args.get(k)
            if v is not None:
                a.query.append((k, v if isinstance(v, str) else json.dumps(v)))
    if "body" in args:
        a.body = json.dumps(args["body"]).encode()
    return a


# ---------------------------------------------------------------- sessions and confirmations


async def new_session(app: FastAPI, who: str, client: str) -> str:
    sid, now = secrets.token_hex(16), now_ms()
    await app.state.records.put(K_SESSION, sha(sid), {"who": who, "client": client, "exp": now + SESSION_TTL_MS},
                                now + SESSION_TTL_MS)  # fmt: skip
    return sid


async def session_ok(app: FastAPI, sid: str, who: str) -> bool:
    v = await app.state.records.get(K_SESSION, sha(sid))
    if not v or v.get("who") != who:
        return False
    now = now_ms()
    if v["exp"] - now < SESSION_TTL_MS - 60_000:
        await app.state.records.put(
            K_SESSION, sha(sid), v | {"exp": now + SESSION_TTL_MS}, now + SESSION_TTL_MS
        )
    return True


async def session_client(app: FastAPI, sid: str) -> str:
    v = await app.state.records.get(K_SESSION, sha(sid))
    return str((v or {}).get("client", ""))


async def issue_confirmation(app: FastAPI, who: str, tool: str, args: dict[str, Any]) -> tuple[str, int]:
    ttl, now = app.state.apicfg.mcp.confirm_ttl_s, now_ms()
    tok = f"cnf_{secrets.token_hex(16)}"
    rec = {"who": who, "tool": tool, "args_hash": args_hash(args), "expires": now + ttl * 1000}
    await app.state.records.put(K_CONFIRM, sha(tok), rec, int(rec["expires"]))
    return tok, int(ttl)


async def check_confirmation(
    app: FastAPI, who: str, tool: str, args: dict[str, Any], token: str
) -> str | None:
    """`None` = may execute (token consumed); otherwise, the reason for refusal."""
    rec_store, key = app.state.records, sha(token)
    p = await rec_store.get(K_CONFIRM, key)
    if not p:
        return "confirm_token inválido ou já usado"
    if p["who"] != who or p["tool"] != tool:
        return "confirm_token de outra ferramenta ou de outro usuário"  # does not consume
    if await rec_store.take(K_CONFIRM, key) is None:
        return "confirm_token inválido ou já usado"
    if p["expires"] <= now_ms():
        return "confirm_token vencido"
    if p["args_hash"] != args_hash(args):
        return "os argumentos mudaram desde a confirmação"
    return None


async def sweep(app: FastAPI) -> None:
    """Deletes the expired `confirm_token`s."""
    now = now_ms()
    db = app.state.auth.db
    if db is not None:
        await db.rec_take_expired(K_CONFIRM, now, 1000)
        return
    rs = app.state.records
    for k, (_, exp) in list(rs.mem.items()):
        if k[0] == K_CONFIRM and exp <= now:
            del rs.mem[k]


async def run_hub(request: Request, p: P, op: Op | None, a: Args) -> dict[str, Any]:
    app = request.app
    if op is None:
        return text_result(app, {"error": "ferramenta sem operação"}, True)
    try:
        _, value = await asyncio.wait_for(call(request, op, p, a), app.state.apicfg.mcp.tool_timeout_s)
        return text_result(app, value)
    except Problem as e:
        return text_result(app, e.body(op.full_path), True)
    except TimeoutError:
        return text_result(app, {"error": "tempo esgotado"}, True)


def _call_params(params: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    name = params.get("name")
    if not isinstance(name, str):
        raise Rpc(INVALID_PARAMS, "name ausente")
    args = params.get("arguments")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise Rpc(INVALID_PARAMS, "arguments deve ser um objeto")
    return name, args


async def _confirm_gate(
    request: Request, p: P, name: str, args: dict[str, Any], client: str, keys: str, scope: str
) -> dict[str, Any] | None:
    """Destructive tool in `confirm` mode: the answer that stops the call (`None` = confirmed, may run)."""
    app, who = request.app, p.key
    token = args.get("confirm_token")
    if not isinstance(token, str):
        tok, ttl = await issue_confirmation(app, who, name, args)
        await audit(
            request,
            p,
            f"MCP: {name}",
            f"confirmação pedida · cliente: {client or '—'} · argumentos: {keys}",
            scope,
        )
        return text_result(app, {
            "confirmation_required": True, "confirm_token": tok, "expires_in_s": ttl, "tool": name,
            "message": f"Ação destrutiva: confirme com o usuário e chame {name} de novo com os mesmos argumentos e confirm_token (válido por {ttl} s).",
        })  # fmt: skip
    if why := await check_confirmation(app, who, name, args, token):
        await audit(request, p, f"MCP: {name}", f"confirmação recusada ({why})", scope)
        return text_result(app, {"error": why}, True)
    return None


async def tools_call(request: Request, p: P, params: dict[str, Any], client: str) -> dict[str, Any]:
    app, cfg = request.app, request.app.state.apicfg
    name, args = _call_params(params)
    wait = await take_rate(app, "mcp", p.rate_key, cfg.mcp.calls_per_min)
    if wait is not None:
        return text_result(
            app, {"error": f"limite de {cfg.mcp.calls_per_min} chamadas por minuto; tente em {wait} s"}, True
        )
    tool = next((t for t in await tools_for(request, p) if t["name"] == name), None)
    if tool is None:
        raise Rpc(INVALID_PARAMS, f"ferramenta desconhecida ou fora do seu acesso: {name}")
    src: dict[str, Any] | None = tool.get("_app")
    op = None if src else opsmod.BY_ID[name]
    scope = src["app"] if src else str(args.get("app") or "platform")
    keys = ", ".join(k for k in args if k != "confirm_token")
    a = hub_args(app, op, args) if op else Args()
    if op and (denied := authorize(op, p, a) or await authorize_scope(request, op, p, a)):
        await audit(request, p, f"MCP: {name}", f"negado ({denied.status}) · argumentos: {keys}", scope)
        return text_result(app, denied.body(op.full_path), True)
    destructive = tool["annotations"]["destructiveHint"] and cfg.mcp.destructive_actions == "confirm"
    if destructive and (stop := await _confirm_gate(request, p, name, args, client, keys, scope)):
        return stop
    result = await run_app(request, p, src, args, text_result) if src else await run_hub(request, p, op, a)
    await audit(
        request,
        p,
        f"MCP: {name}",
        f"{'erro' if result['isError'] else 'ok'} · argumentos: {keys or '—'}",
        scope,
    )
    return result


async def tools_for(request: Request, p: P) -> list[dict[str, Any]]:
    mode = request.app.state.apicfg.mcp.destructive_actions
    apps = await app_tools(request, p, mode, CONFIRM_DESC, destructive_note)
    return [*hub_tools(request.app, p), *apps]


async def resources_read(request: Request, p: P, params: dict[str, Any]) -> dict[str, Any]:
    uri = params.get("uri", "")
    hit = next((r for r in RESOURCES if r[0] == uri), None)
    if hit is None or not request.app.state.apicfg.api.enabled:
        raise Rpc(-32002, f"recurso não encontrado: {uri}")
    try:
        _, v = await call(request, opsmod.BY_ID[hit[3]], p, Args(query=[("limit", "500")]))
    except Problem as e:
        raise Rpc(-32603, e.detail) from e
    return {
        "contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(v, ensure_ascii=False)}]
    }
