"""Operations API: package upload, validation pipeline, deployments (approve, promote, rollback) and build."""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from hub.ops import model as m
from hub.ops import newapp
from hub.ops import pipeline as pl
from hub.ops.deploy_jobs import Upload, run_deploy, run_import
from hub.ops.model import Data, OpsError, now_ms
from hub.ops.opsctx import OpsCtx, ctx

router = APIRouter()


@router.post("/packages")
async def upload(request: Request, ops: OpsCtx = Depends(ctx)) -> Response:  # noqa: PLR0915
    form = await request.form()
    up = form.get("package")
    if up is None or isinstance(up, str):
        raise OpsError("Selecione um arquivo .hubapp (campo package).")
    data = await up.read()
    if not data or len(data) > pl.MAX_PACKAGE_BYTES:
        raise OpsError(
            f"O pacote tem {len(data) / 1024 / 1024:.1f} MB; o limite é "
            f"{pl.MAX_PACKAGE_BYTES // 1024 // 1024} MB. Dependências entram no build, não no pacote: "
            "não copie .venv/ nem vendor/ para build/."
            if data
            else "Selecione um arquivo .hubapp (campo package)."
        )
    raw_env = request.query_params.get("env") or str(form.get("env") or "")
    env = {"staging": "staging", "producao": "producao", "produção": "producao"}.get(raw_env.lower())
    if env is None:
        raise OpsError("Ambiente inválido: use staging ou produção.")
    commit = request.query_params.get("commit")
    if commit is not None and not (
        7 <= len(commit) <= 40 and all(c in "0123456789abcdefABCDEF" for c in commit)
    ):
        raise OpsError("Commit inválido: informe o SHA (7 a 40 hex).")
    file_name = "".join(c for c in (up.filename or "pacote.hubapp") if c.isprintable())[:120]
    ci = request.query_params.get("origin") == "ci"
    origin = "GitHub Actions" if ci else "Import manual"
    peek = pl.peek_app_id(data)
    job = await ops.jobs.create("deploy", file_name, [pl.step(i) for i in range(len(pl.STEPS))], peek, env)
    actor = ops.actor_of(request)
    ops.log("INFO", "platform", actor, f"pacote {file_name} recebido ({-(-len(data) // 1024)} KB) · "
        f"{'Staging' if env == 'staging' else 'Produção'} · {origin}")  # fmt: skip
    coro = run_import(ops, Upload(job["id"], data, env, origin, (commit or "—")[:12].lower(), file_name, actor,
                                  str(request.state.user["id"]), newapp.parse_config(str(form.get("config") or "")) or {}))  # fmt: skip
    if request.query_params.get("wait") == "true":
        await coro
        return JSONResponse(job, status_code=201 if job["ok"] else 422)
    ops.spawn(coro)
    return JSONResponse(job, status_code=202)


@router.post("/manifest/validate")
async def validate_manifest(request: Request, ops: OpsCtx = Depends(ctx)) -> Any:
    raw = await request.body()
    if len(raw) > 64 * 1024:
        raise OpsError("Manifest maior que 64 KB.")
    mf, detail, ok = pl._manifest(raw.decode(errors="replace"), ops.registry.entries())
    if not ok or mf is None:
        return {"valid": False, "error": detail}
    mf["resources"] = mf.get("resources") or {"cpu": 0.5, "memory": "256Mi", "pids": 100}
    mf.setdefault("version", "")
    return {
        "valid": True,
        "error": None,
        "manifest": mf,
        "registered": ops.registry.get(mf["id"]) is not None,
    }


@router.get("/replicas")
async def replicas(ops: OpsCtx = Depends(ctx)) -> Any:
    """Live replicas, who leads, and how many rounds of each leader task this replica ran."""
    return await ops.app.state.leader.info()


@router.get("/jobs/{job_id}")
async def get_job(job_id: int, ops: OpsCtx = Depends(ctx)) -> Any:
    j = await ops.jobs.aget(job_id)
    if j is None:
        raise OpsError("Job não encontrado.", 404)
    return j


@router.get("/deployments")
async def list_deployments(ops: OpsCtx = Depends(ctx)) -> Any:
    return ops.store.read()["deployments"]


@router.post("/deployments/{dep_id}/scripts/approve")
async def approve_scripts(dep_id: int, request: Request, ops: OpsCtx = Depends(ctx)) -> Any:
    actor, uid = ops.actor_of(request), str(request.state.user["id"])
    allow = os.getenv("HUB_SCRIPTS_ALLOW_SELF_APPROVAL") == "1"
    async with ops.store.locked(True) as d:
        try:
            lst = m.approve_scripts(d, dep_id, actor, uid, bool(request.state.token), now_ms(), allow)
        except OpsError as e:
            dep = next((x for x in d["deployments"] if x["id"] == dep_id), None)
            if dep:
                await ops.admin.audit(actor, "Aprovação de JavaScript próprio recusada",
                                  f"{dep['app']} {dep['version']} · {e.message}", icon="alert", scope=dep["app"])  # fmt: skip
            raise
        dep = m._find(d, dep_id)
    detail = f"{ops.name_of(dep['app'])} {dep['version']} · {dep['env']} · " + "; ".join(
        f"{r['path']} {r['sha384']} — {r['reason']}" for r in lst
    )
    await ops.admin.audit(
        actor,
        "JavaScript próprio aprovado (revisão de segurança)",
        detail,
        icon="check",
        scope=dep["app"],
    )
    ops.log("WARN", dep["app"], actor, f"JavaScript próprio aprovado · {dep['app']} {dep['version']} · "
        + ", ".join(r["path"] for r in lst))  # fmt: skip
    return {"deployment": dep, "approved": lst}


@router.post("/deployments/{dep_id}/{action}")
async def deployment_action(  # noqa: PLR0915
    dep_id: int, action: str, request: Request, ops: OpsCtx = Depends(ctx)
) -> Response:
    if action not in ("approve", "promote", "rollback"):
        raise OpsError("Ação inválida.", 404)
    now = now_ms()
    src: Data = {}
    async with ops.store.locked(True) as d:
        if action == "promote":
            src = dict(m._find(d, dep_id))
            dep = m.promote(d, dep_id, now)
        elif action == "approve":
            dep = m.approve(d, dep_id)
        else:
            dep = m.rollback(d, dep_id, now)
    actor = ops.actor_of(request)
    title = {"promote": "Promoção", "approve": "Aprovação", "rollback": "Rollback"}[action]
    job = await ops.jobs.create(
        "deployment",
        f"{title} · {ops.name_of(dep['app'])} {dep['version']} ({dep['env']})",
        [],
        dep["app"],
        "producao",
    )
    if action == "promote":
        await ops.admin.audit(actor, "Versão promovida para produção",
                          f"{ops.name_of(dep['app'])} {dep['version']} · aguardando aprovação (de #{src['id']})",
                          icon="check", scope=dep["app"])  # fmt: skip
        job.update(done=True, ok=True, progress=100, result={"deployment": dep},
                   message=f"{title} registrada — aguardando aprovação para produção.")  # fmt: skip
        return (
            JSONResponse(dep)
            if request.query_params.get("wait") == "true"
            else JSONResponse(job, status_code=202)
        )
    await ops.admin.audit(actor, "Implantação aprovada" if action == "approve" else "Rollback solicitado",
                      f"{ops.name_of(dep['app'])} {dep['version']} · {dep['env']}", icon="check", scope=dep["app"])  # fmt: skip

    async def go() -> Data:
        ok, msg, saved = await run_deploy(ops, dep["id"], job["id"], actor)
        ops.jobs.update(job["id"], lambda j: j.update(done=True, ok=ok, progress=100, message=msg,
                                                  result={"deployment": saved}))  # fmt: skip
        return saved

    if request.query_params.get("wait") == "true":
        saved = await go()
        return JSONResponse(saved, status_code=200 if saved["status"] == m.IMPLANTADO else 422)
    ops.spawn(go())
    return JSONResponse(job, status_code=202)


def mount(ops: OpsCtx) -> None:
    """Startup wiring of this section (state the rest of the hub calls)."""
