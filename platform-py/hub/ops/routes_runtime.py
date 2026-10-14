"""Operations API: containers (limits, logs, inspect, actions), database provisioning and app tasks."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import httpx
from fastapi import Request
from fastapi.responses import Response, StreamingResponse

from hub.admin.routes import bound_summary
from hub.ops import jobs as jb
from hub.ops import model as m
from hub.ops import provision as pv
from hub.ops import runtime as rt
from hub.ops.model import Data, OpsError
from hub.ops.opsctx import OpsCtx, env_key


def mount_runtime(  # noqa: C901, PLR0915 closure mount moved from mount_ops; TASKS.md task 3
    c: OpsCtx,
) -> tuple[
    Callable[[], Awaitable[tuple[Data, dict[str, Any], list[dict[str, Any]], rt.Host]]],
    Callable[[], Awaitable[Any]],
]:
    (
        app,
        client,
        admin,
        registry,
        store,
        eng,
        backups,
        rollout,
        api,
        name_of,
        actor_of,
        log,
        need_app,
        body,
    ) = (
        c.app,
        c.client,
        c.admin,
        c.registry,
        c.store,
        c.eng,
        c.backups,
        c.rollout,
        c.api,
        c.name_of,
        c.actor_of,
        c.log,
        c.need_app,
        c.body,
    )

    # ---- Containers ----------------------------------------------------------------------------------
    def runtime_rows(
        data: Data, live: dict[str, Any], pending: dict[str, Any], bound: dict[str, list[dict[str, Any]]]
    ) -> list[dict[str, Any]]:
        rows = []
        names = {x["name"] for x in live["live"]}
        for e, env in [(e, v) for e in registry.entries() for v in ("producao", "staging")
                       if v == "producao" or rt.container_name(e.id, v) in names]:  # fmt: skip
            r = rt.resources_of(e, data["limits"])
            lab = m.STAGING if env == "staging" else m.PRODUCAO
            dep = next((x for x in data["deployments"] if x["app"] == e.id and x["env"] == lab
                        and x["status"] == m.IMPLANTADO and x["image"]), None)  # fmt: skip
            sup = getattr(app.state, "ops_supervisor", None)
            rows.append({
                "id": e.id, "name": e.name, "env": env, "container": rt.container_name(e.id, env),
                "image": dep["image"] if dep else "sem imagem implantada", "cpu": float(r["cpu"]),
                "memory": r["memory"], "memory_bytes": rt.memory_bytes(str(r["memory"])), "pids": int(r["pids"]),
                "edited": e.id in data["limits"], "network": f"hub-app-{e.id}", "extra_networks": [],
                "env_vars": [bound_summary(v, e.id) for v in bound.get(e.id, []) if any(
                    b["app"] == e.id and b["env"] == env for b in v["bindings"])],
                "restart_pending": e.id in pending, "supervision": sup.status(e.id, env) if sup else None,
                "maintenance": data["maintenance"].get(f"{e.id}|{env}"), "upstream": e.upstream,
            })  # fmt: skip
        return rows

    async def runtime_state() -> tuple[Data, dict[str, Any], list[dict[str, Any]], rt.Host]:
        data = store.read()
        engine_info = await eng.probe()
        pending = await admin.cfg.pending_restart()
        raw = (await admin.cfg.read())["env"]
        bound = {
            e.id: [v for v in raw if any(b["app"] == e.id for b in v["bindings"])] for e in registry.entries()
        }
        host = rt.Host(engine_info.get("ncpu"), (engine_info["mem_total"] or 0) / rt.GIB or None)
        return data, engine_info, runtime_rows(data, engine_info, pending, bound), host

    @api.get("/runtime")
    async def get_runtime() -> Any:
        data, info, rows, host = await runtime_state()
        cpu, mem = rt.allocated(registry.entries(), data["limits"])
        eng_out = {k: info[k] for k in ("reachable", "socket", "version", "error")}
        eng_out["live"] = [
            {k: v for k, v in x.items() if k not in ("host", "networks")} for x in info["live"]
        ]
        apps = [{k: v for k, v in r.items() if k != "upstream"} for r in rows]
        return {
            "host": host.as_json(),
            "allocated": {"cpu": cpu, "memory_gib": mem},
            "engine": eng_out,
            "apps": apps,
        }

    @api.put("/runtime/{app_id}/limits")
    async def put_limits(app_id: str, request: Request) -> Any:
        need_app(app_id)
        lim = await body(request)
        rt.validate_limits(lim)
        lim = {"cpu": float(lim["cpu"]), "memory": str(lim["memory"]), "pids": int(lim["pids"])}
        data = store.read()
        host = rt.Host()
        try:
            rt.admit(host, registry.entries(), data["limits"], app_id, lim)
        except OpsError as e:
            log("WARN", app_id, actor_of(request), f"admission control recusou limites: {e.message}")
            raise
        detail = f"{name_of(app_id)} · {lim['cpu']} vCPU · {lim['memory']} · {lim['pids']} processos"
        async with store.locked(True) as d:
            d["limits"][app_id] = lim
        await admin.audit(
            actor_of(request), "Limites de container alterados", detail, icon="settings", scope=app_id
        )
        parts = []
        for env in ("producao", "staging"):
            name = rt.container_name(app_id, env)
            try:
                if await eng.update_limits(name, lim["cpu"], lim["memory"], lim["pids"]):
                    parts.append(f"aplicado ao container {name}")
            except Exception as e:
                parts.append(f"falha ao aplicar a {name}: {e}"[:200])
        applied = (
            "salvo; " + "; ".join(parts) if parts else "salvo; será aplicado na próxima criação do container"
        )
        log("INFO", app_id, actor_of(request), f"limites {detail} · {applied}")
        return {"limits": lim, "applied": applied}

    def container_of(app_id: str, env: str | None) -> str:
        if rt.is_platform(app_id):  # platform container: read-only (logs/inspect)
            return rt.own(app_id)
        need_app(app_id)
        return rt.container_name(app_id, env_key(env))

    @api.get("/runtime/{app_id}/logs")
    async def runtime_logs(app_id: str, env: str | None = None, tail: int = 200) -> Any:
        name = container_of(app_id, env)
        lines = await eng.tail(name, tail)
        return {
            "container": name,
            "items": [{"stream": k, "level": rt.level_of(k, t), "text": t} for k, t in lines],
        }

    @api.get("/runtime/{app_id}/logs/stream")
    async def runtime_logs_stream(app_id: str, request: Request, env: str | None = None) -> Response:
        name = container_of(app_id, env)

        async def events() -> AsyncIterator[str]:
            yield "event: ready\ndata: conectado\n\n"
            seen = 0
            while not await request.is_disconnected():
                try:
                    lines = await eng.tail(name, 200)
                except OpsError:
                    return
                for k, t in lines[seen:]:
                    yield f"event: line\ndata: {json.dumps({'stream': k, 'level': rt.level_of(k, t), 'text': t})}\n\n"
                seen = len(lines)
                await asyncio.sleep(1.5)

        return StreamingResponse(
            events(), media_type="text/event-stream", headers={"cache-control": "no-store"}
        )

    @api.get("/runtime/{app_id}/inspect")
    async def runtime_inspect(app_id: str, env: str | None = None) -> Any:
        return await eng.inspect(container_of(app_id, env))

    @api.post("/provision/{app_id}/database")
    async def provision_db(app_id: str, request: Request, env: str | None = None) -> Any:
        e = need_app(app_id)
        k, actor = env_key(env), actor_of(request)
        try:
            p = await pv.provision(backups, admin.cfg, actor, app_id, k)
        except OpsError as ex:
            log("ERROR", app_id, actor, f"provisionamento: {ex.message}")
            raise OpsError(ex.message, 503 if ex.status == 400 else ex.status) from ex
        if not p["changed"]:
            await admin.audit(actor, "Banco do app verificado", f"{e.name} · banco {p['database']} já provisionado e acessível",
                              icon="check", scope=app_id)  # fmt: skip
        log("INFO", app_id, actor, f"provisionamento: banco {p['database']} {'provisionado' if p['changed'] else 'já provisionado'} · variável {p['variable']}")  # fmt: skip
        return {"provisioned": p, "restart_pending": app_id in await admin.cfg.pending_restart()}

    async def rotate_db(app_id: str, actor: str, env: str = "producao") -> str:
        var = await pv.rotate(backups, admin.cfg, actor, app_id, env_key(env))
        log("INFO", app_id, actor, f"senha do banco rotacionada · variável {var}")
        return var

    app.state.ops_rotate_db = rotate_db

    @api.post("/runtime/{app_id}/{action}")
    async def runtime_action(app_id: str, action: str, request: Request, env: str | None = None) -> Any:
        e = need_app(app_id)
        if action not in ("apply", "start", "restart", "stop"):
            raise OpsError("Ação inválida.", 404)
        k = env_key(env)
        data = store.read()
        if f"{app_id}|{k}" in data["maintenance"] and action != "apply":
            raise OpsError(f"{name_of(app_id)} em manutenção: ação bloqueada até liberar em Backups.", 409)
        name = rt.container_name(app_id, k)
        sup = getattr(app.state, "ops_supervisor", None)
        if action == "apply":
            lim = rt.resources_of(e, data["limits"])
            await rollout.apply(app_id, k, lim, e.version or "—", None)
            if k == "producao":
                await admin.cfg.clear_pending(app_id)
            await admin.audit(actor_of(request), "Container recriado com as variáveis atuais", f"{e.name} · {name}",
                              icon="box", scope=app_id)  # fmt: skip
            log(
                "INFO",
                app_id,
                actor_of(request),
                f"container {name} recriado com as variáveis atuais (blue/green)",
            )
            return {"container": name, "action": "apply",
                    "detail": f"{e.name} · {name} recriado(s) com a mesma imagem e as variáveis atuais, sem indisponibilidade (blue/green)."}  # fmt: skip
        await eng.action(name, action)
        if sup:
            await sup.mark_stopped(app_id, k, action == "stop")
        label = {"start": "iniciado", "restart": "reiniciado", "stop": "parado"}[action]
        await admin.audit(
            actor_of(request), f"Container {label}", f"{e.name} · {name}", icon="box", scope=app_id
        )
        log("INFO", app_id, actor_of(request), f"container {name} {label}")
        return {"container": name, "action": action}

    # ---- Tarefas -------------------------------------------------------------------------------------
    @api.get("/tasks")
    async def list_tasks() -> Any:
        targets = [(e.id, "producao", e.upstream, admin.tokens.get(e.id)) for e in registry.entries()]
        return await jb.list_all(client, targets, name_of)

    @api.post("/tasks/{app_id}/{task_id}/{action}")
    async def task_action(
        app_id: str, task_id: str, action: str, request: Request, env: str | None = None
    ) -> Any:
        e = need_app(app_id)
        title = {"retry": "Tarefa reprocessada", "cancel": "Cancelamento de tarefa solicitado"}.get(action)
        if title is None:
            raise OpsError("Ação inválida.", 409)
        if not task_id or len(task_id) > 80 or not all(c.isalnum() or c == "-" for c in task_id):
            raise OpsError("Tarefa inválida.", 409)
        if env_key(env) == "staging":
            raise OpsError("Staging não encontrado para esta aplicação.", 404)
        token = admin.tokens.get(app_id)
        if not token:
            raise OpsError("App sem token interno.", 409)
        try:
            r = await client.post(f"{e.upstream.rstrip('/')}/{app_id}/_hub/jobs/{task_id}/{action}",
                                  headers={jb.TOKEN_HEADER: token}, timeout=10)  # fmt: skip
        except httpx.HTTPError as ex:
            raise OpsError(f"app sem resposta ({ex})", 409) from ex
        try:
            out = r.json()
        except ValueError:
            out = {}
        if r.status_code >= 300:
            raise OpsError(
                out.get("error") or f"o app respondeu {r.status_code}",
                404 if "não encontrada" in str(out) else 409,
            )
        job = out.get("job") or {}
        await admin.audit(actor_of(request), title, f"{e.name} · {job.get('kind', '')} · {job.get('id', task_id)}",
                          icon="clock", scope=app_id)  # fmt: skip
        log(
            "INFO",
            app_id,
            actor_of(request),
            f"tarefas: {title} ({job.get('kind', '')} {job.get('id', task_id)})",
        )
        return {"job": job}

    return runtime_state, list_tasks
