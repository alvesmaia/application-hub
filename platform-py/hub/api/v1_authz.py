"""Authorization of `/api/v1` operations: the role's permission, the token's scopes and the token's app scope
(resolved from the call's arguments)."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from starlette.datastructures import UploadFile

from hub.api.ops import Op
from hub.api.v1_base import Args, P, Problem
from hub.auth import api_required


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


def _found(x: Any, what: str) -> Any:
    if not x:
        raise Problem(404, "not-found", f"{what} não encontrado(a).")
    return x


def _by_id(items: list[dict[str, Any]], num: int | None) -> dict[str, Any] | None:
    return next((x for x in items if x["id"] == num), None)


def _deployment(st: Any, args: Args, num: int | None) -> list[str] | None:
    return [_found(_by_id(st.ops.read()["deployments"], num), "Implantação")["app"]]


def _backup(st: Any, args: Args, num: int | None) -> list[str] | None:
    return [_found(_by_id(st.ops.read()["backups"], num), "Backup")["app"]]


def _job(st: Any, args: Args, num: int | None) -> list[str] | None:
    j = _found(st.ops_jobs.get(num) if num is not None else None, "Job")
    return [j["app"]] if j.get("app") else None


def _connection(st: Any, args: Args, num: int | None) -> list[str] | None:
    item = _found(st.conns.get(args.path.get("name", "")), "Conexão")
    apps = sorted({b["app"] for b in item.get("bindings", [])})
    return list(apps) or None


def _inbox_item(st: Any, args: Args, num: int | None) -> list[str] | None:
    ident = args.path.get("id", "").removeprefix("i:")
    it = _found(st.inbox.item(int(ident)) if ident.isdigit() else None, "Item")
    return [it["app"]] if it["app"] not in ("platform", "") else None


_RESOLVERS = {
    "deployments_approve": _deployment,
    "deployments_promote": _deployment,
    "deployments_rollback": _deployment,
    "backups_restore": _backup,
    "jobs_get": _job,
    "connections_test": _connection,
    "inbox_decide": _inbox_item,
}


async def _imported_app(args: Args) -> list[str] | None:
    if not args.body:
        return None
    from hub.ops import pipeline as pl

    pkg = await _package_of(args.body, args.content_type or "")
    app = pl.peek_app_id(pkg) if pkg else None
    if not app:
        raise Problem(400, "bad-request", "Pacote sem manifest.json legível (campo package).")
    return [app]


async def target_apps(request: Request, op: Op, args: Args) -> list[str] | None:
    """Affected apps, resolved from the arguments (`None` = platform operation). Unknown id = 404."""
    if app := args.path.get("app"):
        return [app]
    if op.id == "deployments_import":
        return await _imported_app(args)
    ident = args.path.get("id", "")
    resolve = _RESOLVERS.get(op.id)
    return resolve(request.app.state, args, int(ident) if ident.isdigit() else None) if resolve else None


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
