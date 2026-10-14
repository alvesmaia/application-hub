"""`/api/admin/connections*`: connections CRUD, bindings, credential rotation, tests and provisioned storage."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from hub.admin.routes import actor_of
from hub.auth import now_ms
from hub.connections import provision as prov
from hub.connections.connctx import BAD, ConnCtx, broker_url, ctx
from hub.connections.store import ConnError, bindings_text, target, view
from hub.storage import StorageError
from hub.storage.graph import Endpoints

router = APIRouter()  # under /api/admin/connections


@router.get("")
async def list_(c: ConnCtx = Depends(ctx)) -> Any:
    ep = Endpoints.from_env()
    return {
        "connections": [view(x) for x in c.store.all()],
        "graph": {"default": ep.is_default(), "login": ep.login, "url": ep.graph},
        "broker": {"enabled": c.app.state.broker_enabled, "url": broker_url()},
    }


@router.post("", status_code=201)
async def create(request: Request, c: ConnCtx = Depends(ctx)) -> Any:
    who = actor_of(request)
    x = await c.store.create(await c.body(request))
    await c.audit(
        who,
        "variables",
        "Conexão criada",
        f"{x['name']} · {x['kind']} · {target(x)} · {bindings_text(x['bindings'])}",
    )
    await c.sync(who)
    return JSONResponse(view(x), status_code=201)


@router.put("/{name}")
async def update(request: Request, name: str, c: ConnCtx = Depends(ctx)) -> Any:
    who, i = actor_of(request), await c.body(request)
    x = await c.store.update(name, i)
    cred = " · credencial trocada" if str(i.get("secret", "")).strip() or i.get("certificate_b64") else ""
    await c.audit(
        who,
        "variables",
        "Conexão alterada",
        f"{x['name']} · {target(x)}{cred} · {bindings_text(x['bindings'])}",
    )
    await c.sync(who)
    return view(x)


@router.delete("/{name}", status_code=204)
async def remove(request: Request, name: str, c: ConnCtx = Depends(ctx)) -> Response:
    who = actor_of(request)
    x = await c.store.delete(name)
    await c.audit(
        who, "trash", "Conexão excluída", f"{x['name']} · {target(x)} · {bindings_text(x['bindings'])}"
    )
    await c.sync(who)
    return Response(status_code=204)


@router.put("/{name}/bindings")
async def put_bindings(request: Request, name: str, c: ConnCtx = Depends(ctx)) -> Any:
    who, i = actor_of(request), await c.body(request)
    before = (c.store.get(name) or {}).get("bindings", [])
    if not isinstance(i.get("bindings"), list):
        raise ConnError(BAD)
    x = await c.store.set_bindings(name, i["bindings"])
    await c.audit(
        who,
        "variables",
        "Vínculos da conexão alterados",
        f"{x['name']} · {bindings_text(before)} → {bindings_text(x['bindings'])}",
    )
    await c.sync(who)
    return view(x)


@router.post("/{name}/rotate")
async def rotate(request: Request, name: str, c: ConnCtx = Depends(ctx)) -> Any:
    who = actor_of(request)
    x = await c.store.rotate(name, await c.body(request))
    await c.audit(
        who,
        "variables",
        "Credencial da conexão rotacionada",
        f"{x['name']} · {'certificado' if x['auth'] == 'certificate' else 'segredo'}",
    )
    return view(x)


@router.post("/tokens/{app_id}/{env}/rotate")
async def rotate_token(request: Request, app_id: str, env: str, c: ConnCtx = Depends(ctx)) -> Any:
    await c.store.rotate_token(actor_of(request), app_id, env)
    return {"ok": True, "app": app_id, "env": env}


@router.post("/{name}/test")
async def test(request: Request, name: str, c: ConnCtx = Depends(ctx)) -> Any:
    x = c.store.get(name)
    if x is None:
        raise ConnError("Conexão não encontrada.", 404)
    if not c.permits(request, x):
        raise ConnError("Token sem permissão para os apps desta conexão.", 403)
    try:
        steps = await c.store.backend(x).test()
    except StorageError as e:
        raise ConnError(e.message) from e
    ok = all(s["ok"] is True for s in steps)
    last = {"at": now_ms(), "ok": ok, "steps": steps}
    await c.store.set_last_test(x["name"], x["rev"], last)
    failed = next((s for s in steps if s["ok"] is False), None)
    suffix = f" · falhou em {failed['key']}: {failed['detail']}" if failed else ""
    await c.audit(
        actor_of(request),
        "check",
        "Conexão testada com sucesso" if ok else "Teste de conexão falhou",
        f"{x['name']}{suffix}",
    )
    return last


# ---- armazenamento provisionado ---------------------------------------------------------
@router.get("/storage")
async def storage_list(c: ConnCtx = Depends(ctx)) -> Any:
    reason = c.objects.available()
    return {"available": reason is None, "reason": reason or "", "storage": c.store.storage}


@router.post("/storage/{app_id}")
async def storage_provision(request: Request, app_id: str, env: str = "", c: ConnCtx = Depends(ctx)) -> Any:
    who = actor_of(request)
    status, out = await prov.provision(c.store, c.objects, app_id, c.env_key(env), who, broker_url())
    if out["changed"]:
        await c.audit(
            who,
            "variables",
            "Armazenamento provisionado",
            f"{app_id} ({c.env_key(env)}) · bucket {out['storage']['bucket']} · usuário {out['storage']['user']} restrito ao bucket · conexão {out['storage']['connection']} ligada ao app",
            scope=app_id,
        )
    return JSONResponse(out, status_code=status)


@router.post("/storage/{app_id}/deprovision")
async def storage_deprovision(request: Request, app_id: str, env: str = "", c: ConnCtx = Depends(ctx)) -> Any:
    raw = await request.body()
    try:
        data = json.loads(raw) if raw else {}
    except ValueError as e:
        raise ConnError(BAD) from e
    if not isinstance(data, dict):
        raise ConnError(BAD)
    who, e = actor_of(request), c.env_key(env)
    out = await prov.deprovision(
        c.store, c.objects, app_id, e, who, data, c.step_up_for(request), broker_url()
    )  # type: ignore[arg-type]
    await c.audit(
        who,
        "trash",
        "Armazenamento desprovisionado",
        f"{app_id} ({e}) · {' · '.join(out['steps'])}",
        scope=app_id,
    )
    return out
