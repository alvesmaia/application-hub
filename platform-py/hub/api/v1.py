"""Execution of the public `/api/v1` API: authorization, in-process facade over the internal routes, paginated lists
(envelope), rate limit, audit, RFC 9457 errors and the HTTP routes. Also the engine behind the MCP tools."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, cast
from urllib.parse import parse_qsl, quote

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.datastructures import UploadFile

from hub.api import ops as opsmod
from hub.api.ops import MULTIPART, Op
from hub.auth import api_required

_TITLES = {400: "Requisição inválida", 401: "Não autenticado", 403: "Acesso negado", 404: "Não encontrado",
           405: "Método não permitido", 409: "Conflito", 413: "Grande demais",
           415: "Tipo de conteúdo não suportado", 422: "Não processável", 429: "Muitas requisições",
           503: "Indisponível"}  # fmt: skip
_SLUGS = {400: "bad-request", 401: "unauthorized", 403: "forbidden", 404: "not-found", 409: "conflict",
          413: "too-large", 415: "unsupported-media-type", 422: "unprocessable", 423: "locked",
          429: "rate-limited", 503: "unavailable"}  # fmt: skip


class Problem(Exception):
    """RFC 9457 error (`application/problem+json`)."""

    def __init__(self, status: int, slug: str, detail: str, retry_after: int | None = None) -> None:
        super().__init__(detail)
        self.status, self.slug, self.detail, self.retry_after = status, slug, detail, retry_after

    def body(self, instance: str) -> dict[str, Any]:
        return {"type": f"/api/v1/problems/{self.slug}", "title": _TITLES.get(self.status, "Erro"),
                "status": self.status, "detail": self.detail, "instance": instance}  # fmt: skip

    def response(self, instance: str) -> Response:
        r = Response(json.dumps(self.body(instance)), self.status, media_type="application/problem+json")
        if self.retry_after is not None:
            r.headers["retry-after"] = str(self.retry_after)
        return r


@dataclass
class P:
    """Caller: session or token (with the owner's RBAC)."""

    user: dict[str, Any]
    token: dict[str, Any] | None
    perms: list[str]
    apps: list[str] | None

    @property
    def uid(self) -> str:
        return str(self.user["id"])

    @property
    def name(self) -> str:
        return str(self.user.get("name", ""))

    @property
    def session(self) -> bool:
        return self.token is None

    def has(self, *any_of: str) -> bool:
        return not any_of or any(p in self.perms for p in any_of)

    def can_app(self, app: str) -> bool:
        return (
            "acessar_aplicacoes" in self.perms
            and (self.apps is None or app in self.apps)
            and self.allows_app(app)
        )

    def token_apps(self) -> list[str] | None:
        return (list(self.token.get("apps") or []) or None) if self.token else None

    def allows_action(self, a: str) -> bool:
        return bool(self.token) and a in cast(dict[str, Any], self.token)["actions"]

    def allows_app(self, app: str) -> bool:
        apps = self.token_apps()
        return apps is None or app in apps

    @property
    def key(self) -> str:
        return f"t:{self.token['id']}" if self.token else f"u:{self.uid}"

    @property
    def rate_key(self) -> str:
        if self.token:
            owner = self.token.get("owner")
            return f"u:{owner}" if owner else f"t:{self.token['id']}"
        return f"u:{self.uid}"


def principal(request: Request) -> P | None:
    user = getattr(request.state, "user", None)
    if not user:
        return None
    return P(user, request.state.token, list(request.state.permissions), request.state.apps)


@dataclass
class Args:
    path: dict[str, str] = field(default_factory=dict)
    query: list[tuple[str, str]] = field(default_factory=list)
    body: bytes | None = None
    content_type: str | None = None

    def q(self, k: str) -> str | None:
        return next((v for n, v in self.query if n == k), None)


# ---------------------------------------------------------------- authorization


def need_of(op: Op) -> tuple[str, ...]:
    if op.target is None:
        return op.need
    method, path = op.target
    segs = ["x" if s.startswith("{") else s for s in path.strip("/").split("/")]
    if segs[:2] == ["api", "me"]:
        return ()
    return api_required(method, segs[1:])


def authorize(op: Op, p: P, args: Args | None = None) -> Problem | None:
    def deny(m: str) -> Problem:
        return Problem(403, "forbidden", m)

    if not p.has(*need_of(op)):
        return deny("Sua função não tem a permissão exigida por esta operação (mesmas regras da interface).")
    if p.token:
        if op.write and not p.allows_action("api:write"):
            return deny("Token sem o escopo api:write.")
        if args and (app := args.path.get("app")) and not p.allows_app(app):
            return deny(f"Token sem acesso ao app {app}.")
    return None


async def target_apps(request: Request, op: Op, args: Args) -> list[str] | None:
    """Affected apps, resolved from the arguments (`None` = platform operation). Unknown id = 404."""
    st = request.app.state

    def nf(what: str) -> Problem:
        return Problem(404, "not-found", f"{what} não encontrado(a).")

    if app := args.path.get("app"):
        return [app]
    ident = args.path.get("id", "")
    num = int(ident) if ident.isdigit() else None
    if op.id in ("deployments_approve", "deployments_promote", "deployments_rollback"):
        d = st.ops.read()
        dep = next((x for x in d["deployments"] if x["id"] == num), None)
        if not dep:
            raise nf("Implantação")
        return [dep["app"]]
    if op.id == "backups_restore":
        b = next((x for x in st.ops.read()["backups"] if x["id"] == num), None)
        if not b:
            raise nf("Backup")
        return [b["app"]]
    if op.id == "jobs_get":
        j = st.ops_jobs.get(num) if num is not None else None
        if not j:
            raise nf("Job")
        return [j["app"]] if j.get("app") else None
    if op.id == "connections_test":
        item = st.conns.get(args.path.get("name", ""))
        if not item:
            raise nf("Conexão")
        apps = sorted({b["app"] for b in item.get("bindings", [])})
        return list(apps) or None
    if op.id == "inbox_decide":
        it = st.inbox.item(int(ident.removeprefix("i:"))) if ident.removeprefix("i:").isdigit() else None
        if not it:
            raise nf("Item")
        return [it["app"]] if it["app"] not in ("platform", "") else None
    if op.id == "deployments_import":
        if not args.body:
            return None
        from hub.ops import pipeline as pl

        pkg = await _package_of(args.body, args.content_type or "")
        app = pl.peek_app_id(pkg) if pkg else None
        if not app:
            raise Problem(400, "bad-request", "Pacote sem manifest.json legível (campo package).")
        return [app]
    return None


async def _package_of(body: bytes, content_type: str) -> bytes | None:
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/",
        "headers": [(b"content-type", content_type.encode())],
    }
    try:
        form = await Request(scope, receive).form()
    except Exception:
        return None
    up = form.get("package")
    return await up.read() if isinstance(up, UploadFile) else None


async def authorize_scope(request: Request, op: Op, p: P, args: Args) -> Problem | None:
    """Token's (and owner's) app scope, checked against the apps affected by the call."""
    if not p.token or not (p.token_apps() or p.apps is not None):
        return None
    try:
        apps = await target_apps(request, op, args)
    except Problem as e:
        return e
    if apps is not None:
        for a in apps:
            if not p.allows_app(a) or (p.apps is not None and a not in p.apps):
                return Problem(403, "forbidden", f"Token sem acesso ao app {a}.")
        return None
    if op.write and p.token_apps():
        return Problem(403, "forbidden",
                       "Token restrito a apps não executa operações da plataforma (sem app definido).")  # fmt: skip
    return None


# ---------------------------------------------------------------- in-process facade


async def internal(request: Request, p: P, op: Op, args: Args) -> tuple[int, Any]:
    """Calls the internal route (same handlers as the UI) with `p`'s identity, without going through the network."""
    if op.target is None:
        raise Problem(500, "error", "operação sem rota interna")
    method, tmpl = op.target
    uri = tmpl
    for k in {s.strip("{}") for s in tmpl.split("/") if s.startswith("{")}:
        v = args.path.get(k, "")
        if not _safe_seg(v):
            raise Problem(400, "bad-request", f"Parâmetro {k} inválido.")
        uri = uri.replace("{" + k + "}", v)
    qs = [f"{k}={quote(v, safe='')}" for k in op.passed if (v := args.q(k)) is not None]
    if op.id == "audit_query":
        qs.append("limit=5000")
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
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "scheme": "http",
        "path": uri, "raw_path": uri.encode(), "root_path": "", "query_string": "&".join(qs).encode(),
        "headers": headers, "server": ("hub", 80), "client": request.client and (request.client.host, 0),
        "app": request.app, "state": {}, "hub_facade": (p.user, p.token),
    }  # fmt: skip
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if sent:
            await asyncio.sleep(3600)
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    status, chunks = 500, []

    async def send(msg: dict[str, Any]) -> None:
        nonlocal status
        if msg["type"] == "http.response.start":
            status = msg["status"]
        elif msg["type"] == "http.response.body":
            chunks.append(msg.get("body", b""))

    await request.app(scope, receive, send)
    raw = b"".join(chunks)
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
    return status, v


def _safe_seg(v: str) -> bool:
    return (
        0 < len(v) <= 80
        and all(c.isascii() and (c.isalnum() or c in "._-:") for c in v)
        and v not in (".", "..")
    )


# ---------------------------------------------------------------- listas


def paginate(op: Op, p: P, args: Args, value: Any, cfg: Any) -> Any:
    items = value if not op.list else (value or {}).get(op.list, []) if isinstance(value, dict) else []
    if not isinstance(items, list):
        return items
    if apps := p.token_apps():
        items = [
            i
            for i in items
            if not isinstance(i, dict) or not isinstance(i.get("app"), str) or i["app"] in apps
        ]
    for f in op.filters:
        want = args.q(f)
        if want:

            def match(it: Any, f: str = f, want: str = want) -> bool:
                v = it.get(f) if isinstance(it, dict) else None
                if isinstance(v, bool):
                    return str(v).lower() == want
                if isinstance(v, str):
                    return bool(v.lower() == want.lower())
                return bool(isinstance(v, int | float) and str(v) == want)

            items = [i for i in items if match(i)]
    if "q" not in op.passed and (q := (args.q("q") or "").strip().lower()):
        items = [i for i in items if q in json.dumps(i, ensure_ascii=False).lower()]
    total = len(items)

    def num(k: str) -> int | None:
        v = args.q(k)
        if v is None:
            return None
        if not v.isdigit():
            raise Problem(400, "bad-request", f"{k} inválido.")
        return int(v)

    limit = min(max(num("limit") or cfg.api.page_size, 1), cfg.api.max_page_size)
    offset = min(num("offset") or 0, total)
    page = items[offset : offset + limit]
    nxt = None
    if offset + len(page) < total:
        q = [f"{k}={quote(v, safe='')}" for k, v in args.query if k not in ("offset", "limit")]
        q += [f"limit={limit}", f"offset={offset + len(page)}"]
        path = op.path
        for k, v in args.path.items():
            path = path.replace("{" + k + "}", v)
        nxt = f"/api/v1{path}?{'&'.join(q)}"
    return {"items": page, "total": total, "limit": limit, "offset": offset, "next": nxt}


# ---------------------------------------------------------------- nativas


def visible_app(p: P, app: str) -> bool:
    return p.can_app(app) or (p.has("editar_configuracoes") and p.allows_app(app))


def app_json(request: Request, app_id: str) -> dict[str, Any] | None:
    reg = request.app.state.gateway.registry
    e = reg.get(app_id)
    if e is None or e.manifest is None:
        return None
    m, cfg = e.manifest, request.app.state.apicfg
    extra = m.model_extra or {}
    api, mcp = m.api or {}, m.mcp or {}
    api_on, mcp_on = api.get("enabled", True), mcp.get("enabled", True)
    jobs = [j.get("name") if isinstance(j, dict) else j for j in extra.get("jobs", [])]
    return {
        "id": m.id, "name": m.name or m.id, "version": m.version or "", "mode": extra.get("mode", "fragment"),
        "stack": (m.runtime or {}).get("stack"), "routes": extra.get("routes", []),
        "status": "ok", "url": f"/apps/{m.id}",
        "api": {"enabled": api_on, "base": f"/apps/{m.id}{api.get('base', '/api/v1')}",
                "openapi": f"/apps/{m.id}{api.get('openapi', '/api/openapi.json')}"},
        "mcp": {"enabled": mcp_on and api_on,
                "exposed_by_hub": cfg.mcp.enabled and cfg.mcp.expose_app_tools and mcp_on and api_on,
                "tool_prefix": f"{m.id}."},
        "jobs": jobs,
    }  # fmt: skip


async def _states(request: Request, vis: Iterable[str] | None) -> list[tuple[str, str, str]]:
    from hub.ux import status

    ins = request.app.state.insight
    allowed = None if vis is None else set(vis)
    return status.app_states(
        request.app.state.gateway.registry, allowed, ins.tel.metrics, await ins.ops.incidents()
    )


async def native(request: Request, op: Op, p: P, args: Args) -> Any:
    from hub.insight.metrics import now_s, slo_status

    def nf() -> Problem:
        return Problem(404, "not-found", "App não encontrado ou fora do seu acesso.")

    ins = request.app.state.insight
    reg = request.app.state.gateway.registry
    visible = [e.id for e in reg.entries() if visible_app(p, e.id)]
    match op.native:
        case "apps":
            states = {i: s for i, _, s in await _states(request, visible)}
            return [a | {"status": states.get(a["id"], "ok")} for i in visible if (a := app_json(request, i))]
        case "app" | "app_status":
            app_id = args.path.get("app", "")
            if not visible_app(p, app_id) or not (base := app_json(request, app_id)):
                raise nf()
            states = {i: s for i, _, s in await _states(request, [app_id])}
            base["status"] = states.get(app_id, "ok")
            if op.native == "app":
                return base
            cfgd = await ins.ops.s.cfg.read()
            inc = [i for i in cfgd.get("incidents", []) if i["app"] == app_id and i["resolved"] is None]
            m = ins.tel.metrics
            slos = [slo_status(m, s) | {"app": s["app"], "env": s["env"]} for s in cfgd.get("slos", [])
                    if s["app"] == app_id]  # fmt: skip
            now = now_s()
            w = m.window(app_id, "producao", max(now - 3600, 0), now + 1)
            return {"app": app_id, "status": base["status"], "version": base["version"], "open_incidents": inc,
                    "slos": slos, "last_hour": _win(w, max(now - 3600, 0), now + 1)}  # fmt: skip
        case "status":
            states = await _states(request, visible)
            inc = [i for i in await ins.ops.incidents() if i["resolved"] is None and visible_app(p, i["app"])]
            worst = ("down" if any(s[2] == "down" for s in states)
                     else "degraded" if any(s[2] != "ok" for s in states) else "ok")  # fmt: skip
            return {"overall": worst, "apps": [{"id": i, "name": n, "status": s} for i, n, s in states],
                    "open_incidents": inc}  # fmt: skip
        case _:  # observability
            minutes = int(args.q("minutes") or 15)
            minutes = minutes if minutes in (15, 30, 60) else 15
            only, now, out = args.q("app"), now_s(), []
            for e in reg.entries():
                if (only and only != e.id) or (p.apps is not None and e.id not in p.apps):
                    continue
                if not p.allows_app(e.id):
                    continue
                for env in ("producao", "staging"):
                    w = ins.tel.metrics.window(e.id, env, max(now - minutes * 60, 0), now + 1)
                    if env == "staging" and w.requests == 0 and w.probes == 0:
                        continue
                    out.append({"app": e.id, "env": env, "minutes": minutes, "requests": w.requests,
                                "errors": w.errors, "availability": w.availability, "p95_ms": w.p95,
                                "probes": w.probes, "probes_ok": w.probes_ok})  # fmt: skip
            return {"minutes": minutes, "items": out}


def _win(w: Any, frm: int, to: int) -> dict[str, Any]:
    return {"availability": w.availability, "errors": w.errors, "requests": w.requests, "probes": w.probes,
            "probes_ok": w.probes_ok, "p95": w.p95, "from": frm, "to": to}  # fmt: skip


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


async def audit(request: Request, p: P, title: str, detail: str, scope: str) -> None:
    await request.app.state.admin.audit(p.name, title, detail, scope=scope, icon="settings")


# ---------------------------------------------------------------- rate limit


async def take_rate(app: FastAPI, bucket: str, key: str, per: int, window_ms: int = 60_000) -> int | None:
    """Fixed window per key; returns the seconds to wait when exceeded (`None` = allowed)."""
    now = time.time_ns() // 1_000_000
    db = getattr(app.state.auth, "db", None)
    if db is not None:
        try:
            n, retry = await db.rate_hit(bucket, key, window_ms, per, now)
            return None if n is not None else max(1, -(-int(retry) // 1000))
        except Exception:  # noqa: S110 - database down: counts in this replica
            pass
    win = now // window_ms
    _memory = cast(dict[str, tuple[int, int]], getattr(app.state, "rate_mem", None) or {})
    app.state.rate_mem = _memory
    if len(_memory) > 10_000:
        for k in [k for k, (w, _) in _memory.items() if w != win]:
            del _memory[k]
    w, n = _memory.get(f"{bucket}|{key}", (win, 0))
    if w != win:
        w, n = win, 0
    if n >= per:
        return max(1, ((win + 1) * window_ms - now) // 1000)
    _memory[f"{bucket}|{key}"] = (w, n + 1)
    return None


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


def mount_v1(app: FastAPI) -> None:
    @app.get("/api/docs", include_in_schema=False)
    @app.get("/api/docs/", include_in_schema=False)
    async def docs(request: Request) -> Response:
        cfg = request.app.state.apicfg
        if not cfg.api.enabled or not cfg.api.docs:
            return Problem(404, "not-found", "Documentação desligada ([api.docs].enabled = false).").response(
                request.url.path
            )
        return HTMLResponse(_DOCS, headers={"cache-control": "no-store"})

    @app.api_route(
        "/api/v1/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False
    )
    async def v1(request: Request, rest: str) -> Response:
        cfg, path = request.app.state.apicfg, request.url.path
        if not cfg.api.enabled:
            return Problem(404, "not-found",
                           "A API pública está desligada nesta instalação ([api].enabled = false).").response(path)  # fmt: skip
        p = principal(request)
        if p is None:
            return Problem(401, "unauthorized", "Entre ou use um token de API.").response(path)
        if path == "/api/v1/openapi.json":
            if not cfg.api.docs:
                return Problem(
                    404, "not-found", "Documentação desligada ([api.docs].enabled = false)."
                ).response(path)
            return JSONResponse(opsmod.openapi(), headers={"cache-control": "no-store"})
        if (wait := await take_rate(request.app, "api", p.rate_key, cfg.api.rate_per_min)) is not None:
            msg = f"Limite de {cfg.api.rate_per_min} requisições por minuto atingido."
            return Problem(429, "rate-limited", msg, wait).response(path)
        found = opsmod.route(request.method, "/" + rest)
        if found is True:
            return Problem(405, "method-not-allowed", "Método não suportado neste recurso.").response(path)
        if found is False:
            return Problem(404, "not-found", "Recurso inexistente na API v1.").response(path)
        op, params = found
        ct = request.headers.get("content-type")
        limit = 21 * 1024 * 1024 if op.body == MULTIPART else 256 * 1024
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > limit:
                return Problem(413, "too-large", "Corpo grande demais.").response(path)
        if op.write and op.body and op.body != MULTIPART:
            if (ct or "").split(";")[0].strip().lower() != "application/json":
                return Problem(415, "unsupported-media-type", "Envie application/json.").response(path)
            try:
                json.loads(body)
            except ValueError:
                return Problem(400, "bad-request", "JSON inválido.").response(path)
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
