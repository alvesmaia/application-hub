"""Administration › Users, Roles and permissions and Environment variables: pages and `/api/admin/*` APIs."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any, cast

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, ValidationError

from hub.admin.config import (
    ADMIN_ROLE,
    INTERNAL_TOKEN_ENV,
    KIND_LABEL,
    PERMISSIONS,
    AdminError,
    bound_to,
    injected_name,
    is_secret,
    masked,
)
from hub.admin.store import AdminStore
from hub.admin.tokens import mount_tokens
from hub.auth import PERMISSIONS as PERM_IDS
from hub.auth import AuthStore, now_ms, user_view
from hub.gateway.registry import Registry
from hub.ux.pager import paginate
from hub.ux.texts import page_response, tr

_BAD = "Dados inválidos: verifique os campos enviados."
_GMT3 = -3 * 3600


def relative(ms: int, now: int) -> str:
    diff = max(now - ms, 0) // 1000
    if diff < 60:
        return "agora mesmo"
    if diff < 3600:
        return f"há {diff // 60} min"
    if diff < 7200:
        return "há 1 hora"
    if diff < 86_400:
        return f"há {diff // 3600} horas"
    if diff < 172_800:
        return "ontem"
    if diff < 604_800:
        return f"há {diff // 86_400} dias"
    t = time.gmtime(ms / 1000 + _GMT3)
    return f"{t.tm_mday:02}/{t.tm_mon:02} {t.tm_hour:02}:{t.tm_min:02}"


def initials(name: str) -> str:
    words = name.split()
    if not words:
        return "?"
    return words[0][0].upper() + ("" if len(words) == 1 else words[-1][0].upper())


def dumps(value: Any) -> str:
    """Compact JSON with keys in alphabetical order (format of the previous hub version)."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def err(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


async def json_only(request: Request) -> None:
    """Mutations only with `application/json` (basic CSRF mitigation)."""
    if request.method not in ("GET", "HEAD", "OPTIONS") and (
        request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json"
    ):
        raise AdminError("Envie os dados como application/json.", 415)


async def json_or_empty(request: Request) -> None:
    """Like `json_only`, but without a body (actions such as "provision storage") waive the Content-Type: the defense
    against CSRF is the same-origin check of the auth guard, and these routes do not require JSON."""
    if request.headers.get("content-length", "0") in ("", "0") and "transfer-encoding" not in request.headers:
        return
    await json_only(request)


class UserIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    email: str
    name: str
    roles: list[str]
    password: str
    must_change_password: bool = True


class UserPatch(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str | None = None
    roles: list[str] | None = None
    active: bool | None = None


class PasswordIn(BaseModel):
    password: str


class GrantIn(BaseModel):
    granted: bool


class AppsIn(BaseModel):
    apps: list[str] | None = None


def actor_of(request: Request) -> str:
    return str(request.state.user["name"])


def is_super(request: Request) -> bool:
    return any(r.lower() == ADMIN_ROLE.lower() for r in request.state.user.get("roles", []))


def admin_level(roles: list[dict[str, Any]], role: str) -> bool:
    """Role "administrator": the Administrator or any role with "Manage users"."""
    r = role.strip().lower()
    return r == ADMIN_ROLE.lower() or any(
        x["name"].lower() == r and len(x.get("permissions", [])) > 1 and x["permissions"][1] for x in roles
    )


def same_roles(a: list[str], b: list[str]) -> bool:
    norm = lambda v: sorted({r.strip().lower() for r in v})  # noqa: E731
    return norm(a) == norm(b)


def mount_admin(
    app: FastAPI,
    templates: Jinja2Templates,
    page_ctx: Callable[[Request], dict[str, Any]],
    icon: Callable[[str], str],
    registry: Registry,
    refresh_roles: Callable[[], None],
) -> None:
    api = APIRouter(prefix="/api/admin", dependencies=[Depends(json_only)])
    store = cast(AdminStore, app.state.admin)
    auth = cast(AuthStore, app.state.auth)

    @app.exception_handler(AdminError)
    async def admin_error(request: Request, exc: AdminError) -> JSONResponse:
        return err(exc.status, tr(str(exc), getattr(request.state, "lang", "pt-BR")))

    def refs() -> list[dict[str, str]]:
        out = []
        for e in registry.entries():
            d = e.display
            out.append(
                {
                    "id": e.id,
                    "name": e.name,
                    "short": d.get("short") or e.name,
                    "color": d.get("color", ""),
                    "ink": d.get("ink", ""),
                    "icon": d.get("icon", ""),
                }
            )
        return out

    def meta_json() -> str:
        apps = [{k: a[k] for k in ("color", "id", "ink", "name", "short")} for a in refs()]
        return dumps({"apps": apps, "permissions": list(PERMISSIONS)})

    def render(request: Request, name: str, eyebrow: str, title: str, subtitle: str, **ctx: Any) -> Response:
        data = page_ctx(request) | {"icon": icon, "eyebrow": eyebrow, "title": title, "subtitle": subtitle}
        return page_response(templates, request, name, data | ctx)

    # ---- users ------------------------------------------------------------------------
    async def known_roles() -> list[dict[str, Any]]:
        return await store.cfg.roles()

    def check_grant(request: Request, known: list[dict[str, Any]], roles: list[str]) -> None:
        if is_super(request):
            return
        mine = set(request.state.permissions)
        for r in roles:
            if admin_level(known, r):
                raise AdminError(
                    "Só o Administrador pode atribuir funções com permissões de administrador.", 403
                )
            role = next((x for x in known if x["name"].lower() == r.strip().lower()), None)
            if role and any(
                on and PERM_IDS[i] not in mine for i, on in enumerate(role.get("permissions", []))
            ):
                raise AdminError(
                    f"Você não pode atribuir a função {r}: ela tem permissões que você não tem.", 403
                )

    def check_target(request: Request, known: list[dict[str, Any]], target: dict[str, Any]) -> None:
        if not is_super(request) and any(admin_level(known, r) for r in target["roles"]):
            raise AdminError("Só o Administrador pode alterar contas com permissões de administrador.", 403)

    def check_roles(known: list[dict[str, Any]], roles: list[str]) -> None:
        for r in roles:
            if not any(k["name"].lower() == r.strip().lower() for k in known):
                raise AdminError(f"Função desconhecida: {r}.")

    async def locked_now(email: str) -> bool:
        limit = 5
        if auth.db:
            n = await auth.db.rate_like("login", f"acct:%|{email}", 15 * 60_000, now_ms())
            return n >= limit
        cut = now_ms() - 15 * 60_000
        return any(
            sum(1 for at in v if at > cut) >= limit
            for k, v in app.state.login_failures.items()
            if k.startswith("acct:") and k.endswith(f"|{email}")
        )

    async def view_of(u: dict[str, Any]) -> dict[str, Any]:
        return user_view(u, now_ms(), await locked_now(u["email"]))

    def parse[T: BaseModel](model: type[T], raw: Any) -> T:
        try:
            return model.model_validate(raw)
        except ValidationError as e:
            raise AdminError(_BAD) from e

    async def body(request: Request) -> Any:
        try:
            return await request.json()
        except ValueError as e:
            raise AdminError(_BAD) from e

    async def audit(request: Request, icon_: str, title: str, detail: str) -> None:
        await store.audit(actor_of(request), title, detail, icon=icon_)

    def wrap(e: Exception) -> AdminError:
        return AdminError(str(e), 404 if isinstance(e, LookupError) else 400)

    @api.get("/users")
    async def list_users() -> Any:
        async with auth.locked() as d:
            users = [dict(u) for u in d["users"]]
        return [await view_of(u) for u in users]

    @api.post("/users", status_code=201)
    async def create_user(request: Request) -> Any:
        i = parse(UserIn, await body(request))
        known = await known_roles()
        check_roles(known, i.roles)
        check_grant(request, known, i.roles)
        try:
            u = await auth.create_user(i.email, i.name, i.roles, i.password, i.must_change_password)
        except (ValueError, LookupError) as e:
            raise wrap(e) from e
        await audit(
            request, "users", "Usuário criado", f"{u['name']} ({u['email']}) · {', '.join(u['roles'])}"
        )
        return await view_of(u)

    @api.put("/users/{user_id}")
    async def update_user(request: Request, user_id: str) -> Any:
        p = parse(UserPatch, await body(request))
        known = await known_roles()
        if p.roles is not None:
            check_roles(known, p.roles)
        async with auth.locked() as d:
            before = next((dict(u) for u in d["users"] if u["id"] == user_id), None)
        if before is None:
            raise AdminError("Usuário não encontrado.", 404)
        me = request.state.user.get("id") == user_id
        # Nobody changes their own roles or deactivates themselves (not even the Administrator).
        if me and ((p.roles is not None and not same_roles(p.roles, before["roles"])) or p.active is False):
            raise AdminError("Você não pode alterar as próprias funções nem desativar a própria conta.", 403)
        if not me:
            check_target(request, known, before)
        if p.roles is not None:
            check_grant(request, known, p.roles)
        try:
            u = await auth.update_user(user_id, name=p.name, roles=p.roles, active=p.active)
        except (ValueError, LookupError) as e:
            raise wrap(e) from e
        changes = []
        if before["name"] != u["name"]:
            changes.append(f"nome: {u['name']}")
        if before["roles"] != u["roles"]:
            changes.append(f"funções: {', '.join(u['roles'])}")
        if before["active"] != u["active"]:
            changes.append("reativado" if u["active"] else "desativado (sessões encerradas)")
        if changes:
            await audit(request, "users", "Usuário alterado", f"{u['email']} · {' · '.join(changes)}")
        return await view_of(u)

    @api.delete("/users/{user_id}", status_code=204)
    async def delete_user(request: Request, user_id: str) -> Response:
        if request.state.user.get("id") == user_id:
            raise AdminError("Você não pode excluir a própria conta.", 403)
        async with auth.locked() as d:
            before = next((dict(u) for u in d["users"] if u["id"] == user_id), None)
        if before is None:
            raise AdminError("Usuário não encontrado.", 404)
        check_target(request, await known_roles(), before)
        try:
            u = await auth.delete_user(user_id)
        except (ValueError, LookupError) as e:
            raise wrap(e) from e
        await audit(request, "users", "Usuário excluído", u["email"])
        return Response(status_code=204)

    @api.post("/users/{user_id}/reset-password")
    async def reset_password(request: Request, user_id: str) -> Any:
        i = parse(PasswordIn, await body(request))
        if request.state.user.get("id") == user_id:
            raise AdminError("Para trocar a sua própria senha use Alterar senha no menu do usuário.", 403)
        async with auth.locked() as d:
            target = next((dict(u) for u in d["users"] if u["id"] == user_id), None)
        if target is None:
            raise AdminError("Usuário não encontrado.", 404)
        check_target(request, await known_roles(), target)
        try:
            u = await auth.reset_password(user_id, i.password)
        except (ValueError, LookupError) as e:
            raise wrap(e) from e
        await audit(
            request,
            "users",
            "Senha redefinida",
            f"{u['email']} · senha provisória (troca obrigatória no próximo acesso)",
        )
        return await view_of(u)

    @api.post("/users/{user_id}/unlock")
    async def unlock(request: Request, user_id: str) -> Any:
        try:
            u = await auth.unlock(user_id)
        except LookupError as e:
            raise wrap(e) from e
        if auth.db:
            await auth.db.rate_like("login", f"acct:%|{u['email']}", 0, 0, clear=True)
        for k in [
            k for k in app.state.login_failures if k.startswith("acct:") and k.endswith(f"|{u['email']}")
        ]:
            del app.state.login_failures[k]
        await audit(request, "users", "Usuário desbloqueado", u["email"])
        return await view_of(u)

    @app.get("/admin/users", response_class=HTMLResponse)
    async def users_page(request: Request, tab: str = "") -> Response:
        if tab == "roles":
            return await roles_page(request)
        async with auth.locked() as d:
            users = sorted((dict(u) for u in d["users"]), key=lambda u: u["name"].lower())
        now, me = now_ms(), request.state.user.get("id")
        inbox = request.app.state.inbox
        rows = []
        for u in users:
            v = user_view(u, now, await locked_now(u["email"]))
            if not u["active"]:
                status, cls = "Desativado", "status-pill status-draft"
            elif v["locked"]:
                status, cls = "Bloqueado", "status-pill status-maintenance"
            elif v["must_change_password"]:
                status, cls = "Troca de senha pendente", "status-pill status-pending"
            else:
                status, cls = "Ativo", "status-pill status-active"
            rows.append(
                {
                    "item": json.dumps(v, ensure_ascii=False, separators=(",", ":")),
                    "name": u["name"],
                    "initials": initials(u["name"]),
                    "email": u["email"],
                    "roles": ", ".join(u["roles"]),
                    "status": status,
                    "status_class": cls,
                    "last": relative(u["last_login"], now) if u.get("last_login") else "Nunca",
                    "locked": v["locked"],
                    "active": u["active"],
                    "id": u["id"],
                    "sso": v["sso"],
                    "grants": ", ".join(inbox.grants_for(u["id"])),
                    "me": u["id"] == me,
                }
            )
        meta = dumps(
            {
                "apps": [{"id": a["id"], "name": a["name"]} for a in refs()],
                "roles": [r["name"] for r in await known_roles()],
            }
        )
        total = len(rows)
        rows, pager = paginate(request, rows)
        return render(
            request,
            "admin_users.html",
            "Acesso",
            "Usuários",
            "Contas com acesso à plataforma, suas funções e o estado de cada acesso.",
            rows=rows,
            total=total,
            meta=meta,
            pager=pager,
        )

    # ---- roles ------------------------------------------------------------------------
    async def role_change(request: Request, fn: Callable[..., Any], *args: Any) -> Any:
        if not is_super(request):
            raise AdminError("Só o Administrador altera funções e permissões.", 403)
        out = await fn(actor_of(request), *args)
        refresh_roles()
        return out

    @api.get("/roles")
    async def list_roles() -> Any:
        return await store.cfg.roles()

    @api.post("/roles", status_code=201)
    async def create_role(request: Request) -> Any:
        return await role_change(request, store.cfg.role_create, await body(request))

    @api.put("/roles/{name}")
    async def update_role(request: Request, name: str) -> Any:
        return await role_change(request, store.cfg.role_update, name, await body(request))

    @api.delete("/roles/{name}", status_code=204)
    async def delete_role(request: Request, name: str) -> Response:
        await role_change(request, store.cfg.role_delete, name)
        return Response(status_code=204)

    @api.put("/roles/{name}/permissions/{index}")
    async def set_permission(request: Request, name: str, index: str) -> Any:
        if not index.isdigit():
            raise AdminError("Permissão inválida.")
        g = parse(GrantIn, await body(request))
        return await role_change(request, store.cfg.role_permission, name, int(index), g.granted)

    @api.put("/roles/{name}/apps")
    async def set_role_apps(request: Request, name: str) -> Any:
        a = parse(AppsIn, await body(request))
        return await role_change(request, store.cfg.role_apps, name, a.apps)

    @app.get("/admin/roles")
    async def old_roles() -> Response:
        return RedirectResponse("/admin/users?tab=roles", 308)

    async def roles_page(request: Request) -> Response:
        editable = is_super(request)
        async with auth.locked() as d:
            users = list(d["users"])
        count = lambda n: sum(1 for u in users if any(r.lower() == n.lower() for r in u["roles"]))  # noqa: E731
        roles = []
        for r in await store.cfg.roles():
            n, perms = count(r["name"]), r.get("permissions", [])
            apps = r.get("apps")
            roles.append(
                {
                    "name": r["name"],
                    "description": r["description"],
                    "members": f"{n} usuário{'' if n == 1 else 's'}",
                    "level": r["level"],
                    "level_class": {
                        "Total": "status-pill status-active",
                        "Alto": "status-pill status-pending",
                    }.get(r["level"], "status-pill status-draft"),
                    "locked": r["name"] == ADMIN_ROLE or not editable,
                    "item": dumps(
                        {
                            "name": r["name"],
                            "description": r["description"],
                            "members": r.get("members", 0),
                            "level": r["level"],
                        }
                    ),
                    "menu": f"role:{r['name']}",
                    "perms": [
                        (p, bool(perms[i]) if i < len(perms) else False, str(i))
                        for i, p in enumerate(PERMISSIONS)
                    ],
                    "all_apps": apps is None,
                    "apps": [(a["id"], a["name"], apps is None or a["id"] in apps) for a in refs()],
                }
            )
        subtitle = (
            "Clique em uma função para ver e ajustar as permissões concedidas."
            if editable
            else "Só o Administrador altera funções e permissões; aqui você pode consultá-las."
        )
        return render(
            request,
            "admin_roles.html",
            "Acesso",
            "Funções e permissões",
            subtitle,
            roles=roles,
            editable=editable,
            meta=meta_json(),
        )

    # ---- variables ----------------------------------------------------------------------
    @api.get("/env")
    async def list_env() -> Any:
        return await store.cfg.env_list()

    @api.post("/env", status_code=201)
    async def create_env(request: Request) -> Any:
        return await store.cfg.env_create(actor_of(request), await body(request))

    @api.put("/env/{name}")
    async def update_env(request: Request, name: str) -> Any:
        return await store.cfg.env_update(actor_of(request), name, await body(request))

    @api.delete("/env/{name}", status_code=204)
    async def delete_env(request: Request, name: str) -> Response:
        await store.cfg.env_delete(actor_of(request), name)
        return Response(status_code=204)

    @app.post("/api/ops/rotate/{app_id}/{what}", dependencies=[Depends(json_only)])
    async def rotate_secret(request: Request, app_id: str, what: str) -> Any:
        """The "Rotate token" button of the Per application card (the database password depends on the F5 provisioning)."""
        if app_id not in store.apps:
            raise AdminError("Aplicação não encontrada.", 404)
        if what not in ("token", "database"):
            raise AdminError("Recurso não encontrado.", 404)
        if what == "database":
            fn = getattr(request.app.state, "ops_rotate_db", None)
            if fn is None:
                raise AdminError("Rotação da senha do banco indisponível neste hub.", 503)
            try:
                var = await fn(app_id, actor_of(request), request.query_params.get("env") or "producao")
            except Exception as e:
                raise AdminError(getattr(e, "message", str(e)), getattr(e, "status", 503)) from e
            return {"variable": var, "restart_pending": app_id in await store.cfg.pending_restart()}
        var = await store.cfg.rotate_internal_token(actor_of(request), app_id)
        return {"variable": var["name"], "restart_pending": app_id in await store.cfg.pending_restart()}

    @app.get("/admin/env", response_class=HTMLResponse)
    async def env_page(request: Request, app: str = "") -> Response:
        apps = refs()
        by_id = {a["id"]: a for a in apps}
        flt = app if app in by_id else ""
        data = await store.cfg.read()
        rows = []
        for v in data["env"]:
            if flt and not bound_to(v, flt):
                continue
            m = masked(v)
            title = [v["name"]]
            if v.get("inject_as"):
                title.append(f"no container: {v['inject_as']}")
            if v.get("managed"):
                title.append("gerida pela plataforma")
            rows.append(
                {
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
            )
        pending = data.get("pending_restart", {})
        now = now_ms()
        cards = []
        for a in apps:
            if flt and flt != a["id"]:
                continue
            bound = [v for v in data["env"] if bound_to(v, a["id"])]
            p = pending.get(a["id"])
            cards.append(
                {
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
            )
        return render(
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
            meta=meta_json(),
        )

    mount_tokens(app, api, render, refs, meta_json, actor_of)
    app.include_router(api)
    app.state.app_refs = refs
    _ = INTERNAL_TOKEN_ENV


def bound_summary(v: dict[str, Any], app: str) -> str:
    """`NAME=value` (public) or `NAME (secret)`, with the environments (S = staging, P = production)."""
    envs = "/".join("P" if b["env"] == "producao" else "S" for b in v["bindings"] if b["app"] == app)
    name = injected_name(v)
    shown = f"{name} ({KIND_LABEL[v['kind']].lower()})" if is_secret(v) else f"{name}={v['value'][:40]}"
    return f"{shown} [{envs}]"
