"""Administração › Conexões: página, API `/api/admin/connections*` e «Provisionar armazenamento»."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from typing import Any, cast

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates

from hub.admin.config import AdminError
from hub.admin.routes import actor_of, dumps, err, json_or_empty
from hub.admin.store import AdminStore
from hub.auth import AuthStore, now_ms, sha, verify
from hub.connections import provision as prov
from hub.connections.sql import KIND_LABEL, SQL_KINDS
from hub.connections.store import ConnError, ConnStore, bindings_text, target, view
from hub.storage import StorageError
from hub.storage.graph import Endpoints
from hub.ux.texts import ago, page_response, t

_BAD = "Dados inválidos: verifique os campos enviados."


def broker_url() -> str:
    """Endereço do broker que os apps usam (`HUB_BROKER_PUBLIC_URL`)."""
    return os.getenv("HUB_BROKER_PUBLIC_URL", "").strip() or "http://hub-web:8081"


def mount_connections(
    app: FastAPI,
    templates: Jinja2Templates,
    page_ctx: Callable[[Request], dict[str, Any]],
    icon: Callable[[str], str],
    store: ConnStore,
    admin: AdminStore,
    objects: prov.ObjectStore,
) -> None:
    api = APIRouter(prefix="/api/admin/connections", dependencies=[Depends(json_or_empty)])
    auth = cast(AuthStore, app.state.auth)

    async def storage_for_deploy(app_id: str, env: str) -> dict[str, Any]:
        """F22: `requires.storage` na implantação (mesmo fluxo de «Provisionar armazenamento»)."""
        _, out = await prov.provision(store, objects, app_id, env, "implantação", broker_url())
        return out

    app.state.storage_provision = storage_for_deploy

    @app.exception_handler(ConnError)
    async def conn_error(request: Request, exc: ConnError) -> JSONResponse:
        return err(exc.status, str(exc))

    async def body(request: Request) -> dict[str, Any]:
        try:
            data = await request.json()
        except ValueError as e:
            raise ConnError(_BAD) from e
        if not isinstance(data, dict):
            raise ConnError(_BAD)
        return data

    async def audit(
        request_or_actor: Request | str, icon_: str, title: str, detail: str, scope: str = "platform"
    ) -> None:
        who = request_or_actor if isinstance(request_or_actor, str) else actor_of(request_or_actor)
        await admin.audit(who, title, detail, icon=icon_, scope=scope)

    async def sync(who: str) -> None:
        await store.sync_env(who, broker_url())

    def permits(request: Request, c: dict[str, Any]) -> bool:
        """Token restrito a apps só mexe em conexões vinculadas exclusivamente aos seus apps."""
        allowed = (request.state.token or {}).get("apps") or []
        return not allowed or (bool(c["bindings"]) and all(b["app"] in allowed for b in c["bindings"]))

    @api.get("")
    async def list_() -> Any:
        ep = Endpoints.from_env()
        return {
            "connections": [view(c) for c in store.all()],
            "graph": {"default": ep.is_default(), "login": ep.login, "url": ep.graph},
            "broker": {"enabled": app.state.broker_enabled, "url": broker_url()},
        }

    @api.post("", status_code=201)
    async def create(request: Request) -> Any:
        who = actor_of(request)
        c = await store.create(await body(request))
        await audit(
            who,
            "variables",
            "Conexão criada",
            f"{c['name']} · {c['kind']} · {target(c)} · {bindings_text(c['bindings'])}",
        )
        await sync(who)
        return JSONResponse(view(c), status_code=201)

    @api.put("/{name}")
    async def update(request: Request, name: str) -> Any:
        who, i = actor_of(request), await body(request)
        c = await store.update(name, i)
        cred = " · credencial trocada" if str(i.get("secret", "")).strip() or i.get("certificate_b64") else ""
        await audit(
            who,
            "variables",
            "Conexão alterada",
            f"{c['name']} · {target(c)}{cred} · {bindings_text(c['bindings'])}",
        )
        await sync(who)
        return view(c)

    @api.delete("/{name}", status_code=204)
    async def remove(request: Request, name: str) -> Response:
        who = actor_of(request)
        c = await store.delete(name)
        await audit(
            who, "trash", "Conexão excluída", f"{c['name']} · {target(c)} · {bindings_text(c['bindings'])}"
        )
        await sync(who)
        return Response(status_code=204)

    @api.put("/{name}/bindings")
    async def put_bindings(request: Request, name: str) -> Any:
        who, i = actor_of(request), await body(request)
        before = (store.get(name) or {}).get("bindings", [])
        if not isinstance(i.get("bindings"), list):
            raise ConnError(_BAD)
        c = await store.set_bindings(name, i["bindings"])
        await audit(
            who,
            "variables",
            "Vínculos da conexão alterados",
            f"{c['name']} · {bindings_text(before)} → {bindings_text(c['bindings'])}",
        )
        await sync(who)
        return view(c)

    @api.post("/{name}/rotate")
    async def rotate(request: Request, name: str) -> Any:
        who = actor_of(request)
        c = await store.rotate(name, await body(request))
        await audit(
            who,
            "variables",
            "Credencial da conexão rotacionada",
            f"{c['name']} · {'certificado' if c['auth'] == 'certificate' else 'segredo'}",
        )
        return view(c)

    @api.post("/tokens/{app_id}/{env}/rotate")
    async def rotate_token(request: Request, app_id: str, env: str) -> Any:
        await store.rotate_token(actor_of(request), app_id, env)
        return {"ok": True, "app": app_id, "env": env}

    @api.post("/{name}/test")
    async def test(request: Request, name: str) -> Any:
        c = store.get(name)
        if c is None:
            raise ConnError("Conexão não encontrada.", 404)
        if not permits(request, c):
            raise ConnError("Token sem permissão para os apps desta conexão.", 403)
        try:
            steps = await store.backend(c).test()
        except StorageError as e:
            raise ConnError(e.message) from e
        ok = all(s["ok"] is True for s in steps)
        last = {"at": now_ms(), "ok": ok, "steps": steps}
        await store.set_last_test(c["name"], c["rev"], last)
        failed = next((s for s in steps if s["ok"] is False), None)
        suffix = f" · falhou em {failed['key']}: {failed['detail']}" if failed else ""
        await audit(
            actor_of(request),
            "check",
            "Conexão testada com sucesso" if ok else "Teste de conexão falhou",
            f"{c['name']}{suffix}",
        )
        return last

    # ---- armazenamento provisionado ---------------------------------------------------------
    def env_key(env: str) -> str:
        if env in ("", "producao", "production"):
            return "producao"
        if env == "staging":
            return "staging"
        raise ConnError("Ambiente inválido (use producao ou staging).")

    @api.get("/storage")
    async def storage_list() -> Any:
        reason = objects.available()
        return {"available": reason is None, "reason": reason or "", "storage": store.storage}

    @api.post("/storage/{app_id}")
    async def storage_provision(request: Request, app_id: str, env: str = "") -> Any:
        who = actor_of(request)
        status, out = await prov.provision(store, objects, app_id, env_key(env), who, broker_url())
        if out["changed"]:
            await audit(
                who,
                "variables",
                "Armazenamento provisionado",
                f"{app_id} ({env_key(env)}) · bucket {out['storage']['bucket']} · usuário {out['storage']['user']} restrito ao bucket · conexão {out['storage']['connection']} ligada ao app",
                scope=app_id,
            )
        return JSONResponse(out, status_code=status)

    async def step_up_for(request: Request) -> Callable[[str], Any]:
        async def check(password: str) -> str | None:
            u = request.state.user
            if request.state.token is not None or not u.get("id"):
                return "Apagar um bucket com objetos exige reautenticação com senha numa sessão do navegador (tokens e agentes não confirmam)."
            tries = app.state.stepup_fails.setdefault(u["id"], [])
            cut = now_ms() - 15 * 60_000
            tries[:] = [x for x in tries if x > cut]
            if len(tries) >= 5:
                return "Muitas tentativas. Aguarde alguns minutos e tente de novo."
            async with auth.locked() as d:
                row = next((x for x in d["users"] if x["id"] == u["id"]), None)
            if password and row and verify(password, row.get("password_hash")):
                return None
            tries.append(now_ms())
            return "Senha incorreta: a remoção do bucket com objetos não foi confirmada."

        return check

    @api.post("/storage/{app_id}/deprovision")
    async def storage_deprovision(request: Request, app_id: str, env: str = "") -> Any:
        raw = await request.body()
        try:
            data = json.loads(raw) if raw else {}
        except ValueError as e:
            raise ConnError(_BAD) from e
        if not isinstance(data, dict):
            raise ConnError(_BAD)
        who, e = actor_of(request), env_key(env)
        out = await prov.deprovision(
            store, objects, app_id, e, who, data, await step_up_for(request), broker_url()
        )  # type: ignore[arg-type]
        await audit(
            who,
            "trash",
            "Armazenamento desprovisionado",
            f"{app_id} ({e}) · {' · '.join(out['steps'])}",
            scope=app_id,
        )
        return out

    app.state.stepup_fails = {}
    app.include_router(api)

    # ---- página ----------------------------------------------------------------------------
    @app.get("/admin/connections", response_class=HTMLResponse)
    async def connections_page(request: Request) -> Response:
        lang = request.state.lang
        tr_ = lambda k: t(k, lang)  # noqa: E731
        apps = app.state.app_refs()
        by_id = {a["id"]: a for a in apps}
        now = now_ms()
        rows = []
        for c in store.all():
            if c["kind"] in SQL_KINDS:
                ro = tr_("conn.sql_readonly") if c["readonly"] else tr_("conn.sql_readwrite")
                credential = f"{c['username']} · •••••••• · {ro}"
            elif c["kind"] == "sharepoint":
                credential = f"{tr_('conn.secret')} ••••••••"
            else:
                credential = f"{c['access_key']} · ••••••••"
            lt = c["last_test"]
            if lt is None:
                label, cls = tr_("conn.never_tested"), "status-pill status-draft"
            elif lt["ok"]:
                label, cls = (
                    f"{tr_('conn.test_ok')} · {ago(lt['at'], now, lang)}",
                    "status-pill status-active",
                )
            else:
                label, cls = (
                    f"{tr_('conn.test_failed')} · {ago(lt['at'], now, lang)}",
                    "status-pill status-maintenance",
                )
            rows.append(
                {
                    "name": c["name"],
                    "description": c["description"],
                    "kind": KIND_LABEL.get(c["kind"])
                    or ("SharePoint" if c["kind"] == "sharepoint" else "S3 / MinIO"),
                    "target": target(c),
                    "credential": credential,
                    "chips": [
                        f"{by_id.get(b['app'], {}).get('short', b['app'])} · "
                        f"{tr_('conn.env_staging') if b['env'] == 'staging' else tr_('conn.env_prod')}"
                        for b in c["bindings"]
                    ],
                    "test_label": label,
                    "test_class": cls,
                    "item": json.dumps(view(c), ensure_ascii=False, separators=(",", ":")),
                }
            )
        pending = await admin.cfg.pending_restart()
        ep = Endpoints.from_env()
        storage = [
            (
                by_id.get(p["app"], {}).get("name", p["app"]),
                tr_("conn.env_staging") if p["env"] == "staging" else tr_("conn.env_prod"),
                p["bucket"],
                p["user"],
                f"{p['app']}|{p['env']}",
            )
            for p in store.storage
        ]
        ctx = page_ctx(request) | {
            "icon": icon,
            "eyebrow": "Aplicações",
            "title": tr_("conn.title"),
            "subtitle": tr_("conn.subtitle"),
            "rows": rows,
            "pending_apps": [
                (a["id"], a["name"], len(pending[a["id"]]["vars"])) for a in apps if a["id"] in pending
            ],
            "storage": storage,
            "app_opts": [(a["id"], a["name"]) for a in apps],
            "meta": dumps({"apps": [{"id": a["id"], "short": a["short"]} for a in apps]}),
            "sim_warning": None
            if ep.is_default()
            else f"{tr_('conn.sim_warning')} ({ep.login} · {ep.graph})",
            "broker_off": not app.state.broker_enabled,
        }
        return page_response(templates, request, "admin_connections.html", ctx)

    _ = (AdminError, sha, time)
