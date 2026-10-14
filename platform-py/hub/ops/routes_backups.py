"""Operations API: backup policies, runs, restore/clone/test, set-aside databases, maintenance and alerts."""

from __future__ import annotations

import os
from functools import partial
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from hub.ops.backup_jobs import alert, start_backup, start_restore
from hub.ops.backup_policy import parse_policy
from hub.ops.backups import parse_targets
from hub.ops.model import OpsError
from hub.ops.opsctx import OpsCtx, ctx, env_key

router = APIRouter()


@router.get("/backups")
async def list_backups(ops: OpsCtx = Depends(ctx)) -> Any:
    d = ops.store.read()
    return {"policies": d["policies"], "history": d["backups"], "schedules": d["schedules"],
            "timezone": os.getenv("HUB_BACKUP_TZ", "America/Sao_Paulo")}  # fmt: skip


@router.put("/backups/policies/{app_id}")
async def put_policy(app_id: str, request: Request, ops: OpsCtx = Depends(ctx)) -> Any:
    ops.need_app(app_id)
    pol = parse_policy(app_id, await ops.body(request))
    async with ops.store.locked(True) as d:
        d["policies"] = [x for x in d["policies"] if x["app"] != app_id] + [pol]
        d["schedules"] = {k: v for k, v in d["schedules"].items() if not k.startswith(f"{app_id}|")}
    await ops.admin.audit(ops.actor_of(request), "Política de backup alterada",
                          f"{ops.name_of(app_id)} · {pol['schedule']} · últimos {pol['keep']} · volumes {', '.join(pol['volumes'])}",
                          icon="clock", scope=app_id)  # fmt: skip
    return pol


@router.post("/backups/{app_id}/run")
async def run_backup(
    app_id: str, request: Request, env: str | None = None, ops: OpsCtx = Depends(ctx)
) -> Response:
    ops.need_app(app_id)
    return JSONResponse(
        await start_backup(ops, app_id, env_key(env), "Sob demanda", ops.actor_of(request)), status_code=202
    )


async def known_dbs(ops: OpsCtx) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    for a in ops.registry.ids():
        for env in ("producao", "staging"):
            db, _ = parse_targets(await ops.backups.app_env(a, env))
            if db and all(x[2] != db for x in out):
                out.append((a, env, db))
    return out


@router.get("/backups/aside")
async def list_aside(ops: OpsCtx = Depends(ctx)) -> Any:
    try:
        return {"available": True, "aside": await ops.backups.list_aside(await known_dbs(ops))}
    except OpsError as e:
        if e.status == 503:
            return {"available": False, "aside": []}
        raise


@router.post("/backups/aside/{name}/{action}")
async def aside_action(name: str, action: str, request: Request, ops: OpsCtx = Depends(ctx)) -> Any:
    if action not in ("rename-back", "drop"):
        raise OpsError("Ação inválida.", 404)
    found = next((x for x in await ops.backups.list_aside(await known_dbs(ops)) if x["aside"] == name), None)
    if found is None:
        raise OpsError("Banco de lado não encontrado (ou restauração em curso).", 404)
    await ops.jobs.claim(found["app"])
    try:
        a = await ops.backups.aside_action(await known_dbs(ops), name, action)
    finally:
        ops.jobs.busy.discard(found["app"])
    what = (f"banco de lado {name} apagado" if action == "drop"
            else f"banco de lado {name} devolvido ao nome {a['db']}")  # fmt: skip
    actor = ops.actor_of(request)
    ops.log("WARN", a["app"], actor, f"backups: {what}")
    await ops.admin.audit(actor, "Banco de lado", what, icon="check", scope=a["app"])
    return {"done": True, "detail": what}


@router.post("/backups/credentials/rotate")
async def rotate_credentials(request: Request, ops: OpsCtx = Depends(ctx)) -> Any:
    if ops.admin.vault is None:
        raise OpsError(
            "Cofre desligado (HUB_VAULT=0): as credenciais de backup já são novas a cada backup.", 409
        )
    lines = await ops.backups.rotate_all(ops.registry.ids())
    failed = sum("falhou" in ln for ln in lines)
    await ops.admin.audit(ops.actor_of(request), "Credenciais de backup rotacionadas",
                      " · ".join(lines) or "nenhum banco/bucket de app com backup", icon="key")  # fmt: skip
    return {"rotated": len(lines) - failed, "failed": failed, "items": lines}


@router.post("/backups/records/{rec_id}/{action}")
async def backup_action(rec_id: int, action: str, request: Request, ops: OpsCtx = Depends(ctx)) -> Response:
    return JSONResponse(await start_restore(ops, rec_id, action, ops.actor_of(request)), status_code=202)


@router.post("/maintenance/{app_id}/clear")
async def clear_maintenance(
    app_id: str, request: Request, env: str | None = None, ops: OpsCtx = Depends(ctx)
) -> Any:
    ops.need_app(app_id)
    k = env_key(env)
    async with ops.store.locked(True) as d:
        if d["maintenance"].pop(f"{app_id}|{k}", None) is None:
            raise OpsError("O app não está em manutenção.", 409)
    await ops.admin.audit(
        ops.actor_of(request),
        "Manutenção liberada",
        f"{ops.name_of(app_id)} ({k})",
        icon="check",
        scope=app_id,
    )
    return {"cleared": True}


def mount(ops: OpsCtx) -> None:
    """Startup wiring of this section (state the rest of the hub calls)."""
    ops.app.state.platform_alert = partial(alert, ops)
    ops.app.state.ops_start_backup = partial(start_backup, ops)
