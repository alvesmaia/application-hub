"""One-time upload links (`deployments_upload`): an agent asks for a link through MCP, then sends the `.hubapp` with
curl to `{mcp.path}/uploads/<ticket>`. That endpoint has no session or bearer (the ticket is the credential) and sits
under the MCP prefix because the public domain only lets `/mcp*` through to the hub. The import itself is the same
`POST /api/ops/packages`, dispatched in process as the ticket's owner (permission re-checked at upload time)."""

from __future__ import annotations

import hashlib
import secrets
import string
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from hub.api import records as rec_mod
from hub.api.ops import BY_ID
from hub.api.v1 import internal_raw
from hub.api.v1_authz import authorize, target_apps
from hub.api.v1_base import Args, P, Problem, audit, take_rate
from hub.auth import client_ip, permissions
from hub.ops.pipeline import MAX_PACKAGE_BYTES

K_UPLOAD = "mcp.upload"
TTL_MS = 10 * 60_000
MAX_BODY = MAX_PACKAGE_BYTES + 1024 * 1024  # the package plus the form overhead and the optional `config`
PER_IP_PER_MIN = 30
GONE = "Link de envio inválido, vencido ou já usado: peça outro com deployments_upload."
NEXT = (
    "Envie o pacote com o comando curl (uso único, até expires_at; ?wait=true aguarda o pipeline e "
    "-F config=@values.json leva variáveis e segredos como no CLI). A resposta traz o job: acompanhe com "
    "jobs_get e deployments_list. Em produção a implantação fica aguardando deployments_approve."
)


def _hash(ticket: str) -> str:
    return hashlib.sha256(ticket.encode()).hexdigest()


def _base(request: Request) -> str:
    oa = request.app.state.oauth
    return str(oa.issuer(request) or oa.request_origin(request) or "")


def _target(args: Args) -> tuple[str, str | None]:
    env, commit = args.q("env"), args.q("commit")
    if env not in ("staging", "producao"):
        raise Problem(400, "bad-request", f"env inválido: {env!r}; use staging ou producao.")
    if commit is not None and not (7 <= len(commit) <= 40 and set(commit) <= set(string.hexdigits)):
        raise Problem(400, "bad-request", f"commit inválido: {commit!r}; informe o SHA (7 a 40 hex).")
    return env, commit


def _link(url: str, env: str, commit: str | None, expires_at: int) -> dict[str, Any]:
    return {
        "upload_url": url, "expires_at": expires_at, "method": "POST", "field": "package",
        "max_mb": MAX_PACKAGE_BYTES // 1024 // 1024, "env": env, "commit": commit,
        "curl": f"curl -fsS -F package=@<arquivo>.hubapp '{url}'", "next": NEXT,
    }  # fmt: skip


async def issue_link(request: Request, p: P, args: Args) -> dict[str, Any]:
    """Stores the hashed ticket (owner, environment, commit, token app scope) and answers the link."""
    env, commit = _target(args)
    ticket, now = secrets.token_urlsafe(32), rec_mod._now()
    record = {"uid": p.uid, "env": env, "commit": commit, "apps": p.token_apps(), "created_at": now}
    await request.app.state.records.put(K_UPLOAD, _hash(ticket), record, now + TTL_MS)
    ref = _hash(ticket)[:8]  # only a short hash prefix reaches the audit trail
    await audit(request, p, "Link de envio emitido", f"{env} · válido por 10 min · ref {ref}", "platform")
    url = f"{_base(request)}{request.app.state.apicfg.mcp.path}/uploads/{ticket}"
    return _link(url, env, commit, now + TTL_MS)


# ---------------------------------------------------------------- upload


async def _admit(request: Request, ticket: str) -> None:
    """Rate limit by IP, multipart only and a live ticket, all before reading the body (nothing is spent yet)."""
    wait = await take_rate(request.app, "upload", f"ip:{client_ip(request)}", PER_IP_PER_MIN)
    if wait is not None:
        raise Problem(429, "rate-limited", f"Muitos envios deste endereço; tente em {wait} s.", wait)
    ct = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if ct != "multipart/form-data":
        raise Problem(
            415, "unsupported-media-type", "Envie o pacote como multipart/form-data (campo package)."
        )
    if await request.app.state.records.get(K_UPLOAD, _hash(ticket)) is None:
        raise Problem(404, "not-found", GONE)


async def _body(request: Request) -> bytes:
    too_big = Problem(
        413, "too-large", f"Pacote grande demais: o limite é {MAX_PACKAGE_BYTES // 1024 // 1024} MB."
    )
    if int(request.headers.get("content-length") or 0) > MAX_BODY:
        raise too_big
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_BODY:
            raise too_big
    return body


async def _owner(request: Request, record: dict[str, Any]) -> P:
    """The ticket's owner as of now: still active, with the permissions and apps of their current roles."""
    async with request.app.state.auth.locked() as d:
        user = next((u.copy() for u in d["users"]
                     if u["id"] == record["uid"] and u["active"] and not u["must_change_password"]), None)  # fmt: skip
    if user is None:
        raise Problem(403, "forbidden", "Quem pediu o link não está mais ativo no hub.")
    perms, apps = permissions(user, request.app.state.roles)
    if apps is not None:  # access granted by a request approved in the Inbox
        apps = [*apps, *request.app.state.inbox.grants_for(user["id"])]
    return P(user, None, perms, apps)


async def _check_app(request: Request, p: P, record: dict[str, Any], args: Args) -> None:
    """Same app scope as an upload with the token that asked for the link (and the owner's role apps)."""
    for app in await target_apps(request, BY_ID["deployments_import"], args) or []:
        if (record["apps"] and app not in record["apps"]) or (p.apps is not None and app not in p.apps):
            raise Problem(403, "forbidden", f"Link sem acesso ao app {app}.")


def _import_args(request: Request, record: dict[str, Any], body: bytes) -> Args:
    query = [("env", record["env"])]
    if record["commit"]:
        query.append(("commit", record["commit"]))
    if request.query_params.get("wait") == "true":
        query.append(("wait", "true"))
    return Args(query=query, body=body, content_type=request.headers.get("content-type"))


async def _import(request: Request, ticket: str, record: dict[str, Any], body: bytes) -> Response:
    p, op = await _owner(request, record), BY_ID["deployments_import"]
    if denied := authorize(op, p):
        raise denied
    args = _import_args(request, record, body)
    await _check_app(request, p, record, args)
    status, raw = await internal_raw(request, p, op, args)
    await audit(
        request, p, "Link de envio usado", f"{record['env']} · {status} · ref {_hash(ticket)[:8]}", "platform"
    )
    return Response(raw, status, media_type="application/json", headers={"cache-control": "no-store"})


async def upload(request: Request, ticket: str) -> Response:
    """`POST {mcp.path}/uploads/{ticket}`: the ticket is spent atomically (`take`), so only one upload runs."""
    try:
        await _admit(request, ticket)
        body = await _body(request)
        record = await request.app.state.records.take(K_UPLOAD, _hash(ticket))
        if record is None:  # spent by a concurrent upload, or expired while the body arrived
            raise Problem(404, "not-found", GONE)
        return await _import(request, ticket, record, body)
    except Problem as e:
        headers = {"cache-control": "no-store"} | (
            {"retry-after": str(e.retry_after)} if e.retry_after else {}
        )
        return JSONResponse({"error": e.detail}, e.status, headers=headers)


def mount_uploads(app: FastAPI) -> None:
    app.add_api_route(f"{app.state.apicfg.mcp.path}/uploads/{{ticket}}", upload, methods=["POST"],
                      include_in_schema=False)  # fmt: skip
