"""Administration › Environment variables: page `/admin/env`, `/api/admin/env*` and secret rotation."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response

from hub.admin.adminctx import AdminCtx, actor_of, body, bound_summary, ctx, dumps, json_only, relative
from hub.admin.config import KIND_LABEL, AdminError, bound_to, injected_name, is_secret, masked
from hub.auth import now_ms

api = APIRouter()
pages = APIRouter()


@api.get("/env")
async def list_env(c: AdminCtx = Depends(ctx)) -> Any:
    return await c.store.cfg.env_list()


@api.post("/env", status_code=201)
async def create_env(request: Request, c: AdminCtx = Depends(ctx)) -> Any:
    return await c.store.cfg.env_create(actor_of(request), await body(request))


@api.put("/env/{name}")
async def update_env(request: Request, name: str, c: AdminCtx = Depends(ctx)) -> Any:
    return await c.store.cfg.env_update(actor_of(request), name, await body(request))


@api.delete("/env/{name}", status_code=204)
async def delete_env(request: Request, name: str, c: AdminCtx = Depends(ctx)) -> Response:
    await c.store.cfg.env_delete(actor_of(request), name)
    return Response(status_code=204)


async def _rotate_db(request: Request, c: AdminCtx, app_id: str) -> Any:
    fn = getattr(request.app.state, "ops_rotate_db", None)
    if fn is None:
        raise AdminError("Rotação da senha do banco indisponível neste hub.", 503)
    try:
        var = await fn(app_id, actor_of(request), request.query_params.get("env") or "producao")
    except Exception as e:
        raise AdminError(getattr(e, "message", str(e)), getattr(e, "status", 503)) from e
    return {"variable": var, "restart_pending": app_id in await c.store.cfg.pending_restart()}


@pages.post("/api/ops/rotate/{app_id}/{what}", dependencies=[Depends(json_only)])
async def rotate_secret(request: Request, app_id: str, what: str, c: AdminCtx = Depends(ctx)) -> Any:
    """The "Rotate token" button of the Per application card (the database password depends on the F5 provisioning)."""
    if app_id not in c.store.apps:
        raise AdminError("Aplicação não encontrada.", 404)
    if what not in ("token", "database"):
        raise AdminError("Recurso não encontrado.", 404)
    if what == "database":
        return await _rotate_db(request, c, app_id)
    var = await c.store.cfg.rotate_internal_token(actor_of(request), app_id)
    return {"variable": var["name"], "restart_pending": app_id in await c.store.cfg.pending_restart()}


def _env_row(v: dict[str, Any]) -> dict[str, Any]:
    m = masked(v)
    title = [v["name"]]
    if v.get("inject_as"):
        title.append(f"no container: {v['inject_as']}")
    if v.get("managed"):
        title.append("gerida pela plataforma")
    return {
        "name": v["name"],
        "shown": m["value"],
        "secret": is_secret(v),
        "kind": KIND_LABEL[v["kind"]],
        "injected": v.get("inject_as"),
        "managed": bool(v.get("managed")),
        "apps": " ".join(sorted({b["app"] for b in v["bindings"]})),
        "name_title": " · ".join(title),
        "menu": f"env:{v['name']}",
        "item": dumps(
            {
                "name": v["name"],
                "value": "" if is_secret(v) else v["value"],
                "kind": v["kind"],
                "bindings": v["bindings"],
                "inject_as": v.get("inject_as"),
                "managed": bool(v.get("managed")),
            }
        ),
    }


def _app_card(
    a: dict[str, str], env: list[dict[str, Any]], p: dict[str, Any] | None, now: int
) -> dict[str, Any]:
    bound = [v for v in env if bound_to(v, a["id"])]
    return {
        "id": a["id"],
        "name": a["name"],
        "icon": a["icon"],
        "icon_style": f"background:{a['color']};color:{a['ink']}",
        "has_db": any(injected_name(v) == "HUB_DB_URL" for v in bound),
        "vars": [bound_summary(v, a["id"]) for v in bound],
        "is_pending": p is not None,
        "pending": f"Reinício pendente desde {relative(p['since'], now)} · alteradas: {', '.join(p['vars'])}"
        if p
        else None,
    }


@pages.get("/admin/env", response_class=HTMLResponse)
async def env_page(request: Request, app: str = "", c: AdminCtx = Depends(ctx)) -> Response:
    apps = c.refs()
    flt = app if app in {a["id"] for a in apps} else ""
    data = await c.store.cfg.read()
    rows = [_env_row(v) for v in data["env"] if not flt or bound_to(v, flt)]
    pending = data.get("pending_restart", {})
    now = now_ms()
    cards = [_app_card(a, data["env"], pending.get(a["id"]), now) for a in apps if not flt or flt == a["id"]]
    return c.render(
        request,
        "admin_env.html",
        "Aplicações",
        "Variáveis de ambiente",
        "Variáveis e connection strings vinculadas a cada aplicação e ambiente; injetadas no container a cada implantação ou reinício.",
        apps=apps,
        rows=rows,
        cards=cards,
        pending_apps=[
            (a["id"], a["name"], len(pending[a["id"]]["vars"])) for a in apps if a["id"] in pending
        ],
        meta=c.meta_json(),
    )
