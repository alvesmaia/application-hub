"""Shared state and helpers of the Administration routes (Users, Roles, Variables, Settings, Tokens)."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, ValidationError

from hub.admin.config import ADMIN_ROLE, KIND_LABEL, PERMISSIONS, AdminError, injected_name, is_secret
from hub.admin.store import AdminStore
from hub.auth import PERMISSIONS as PERM_IDS
from hub.auth import AuthStore, now_ms, user_view
from hub.gateway.registry import Registry
from hub.ux.texts import page_response

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


def bound_summary(v: dict[str, Any], app: str) -> str:
    """`NAME=value` (public) or `NAME (secret)`, with the environments (S = staging, P = production)."""
    envs = "/".join("P" if b["env"] == "producao" else "S" for b in v["bindings"] if b["app"] == app)
    name = injected_name(v)
    shown = f"{name} ({KIND_LABEL[v['kind']].lower()})" if is_secret(v) else f"{name}={v['value'][:40]}"
    return f"{shown} [{envs}]"


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


def wrap(e: Exception) -> AdminError:
    return AdminError(str(e), 404 if isinstance(e, LookupError) else 400)


def check_roles(known: list[dict[str, Any]], roles: list[str]) -> None:
    for r in roles:
        if not any(k["name"].lower() == r.strip().lower() for k in known):
            raise AdminError(f"Função desconhecida: {r}.")


def check_target(request: Request, known: list[dict[str, Any]], target: dict[str, Any]) -> None:
    if not is_super(request) and any(admin_level(known, r) for r in target["roles"]):
        raise AdminError("Só o Administrador pode alterar contas com permissões de administrador.", 403)


def check_grant(request: Request, known: list[dict[str, Any]], roles: list[str]) -> None:
    if is_super(request):
        return
    mine = set(request.state.permissions)
    for r in roles:
        if admin_level(known, r):
            raise AdminError("Só o Administrador pode atribuir funções com permissões de administrador.", 403)
        role = next((x for x in known if x["name"].lower() == r.strip().lower()), None)
        if role and any(on and PERM_IDS[i] not in mine for i, on in enumerate(role.get("permissions", []))):
            raise AdminError(
                f"Você não pode atribuir a função {r}: ela tem permissões que você não tem.", 403
            )


class AdminCtx:
    """One per app (`app.state.admin_ctx`); route handlers get it with `Depends(ctx)`."""

    def __init__(
        self,
        app: FastAPI,
        templates: Jinja2Templates,
        page_ctx: Callable[[Request], dict[str, Any]],
        icon: Callable[[str], str],
        registry: Registry,
        refresh_roles: Callable[[], None],
    ) -> None:
        self.app, self.templates, self.page_ctx, self.icon = app, templates, page_ctx, icon
        self.registry, self.refresh_roles = registry, refresh_roles
        self.store = cast(AdminStore, app.state.admin)
        self.auth = cast(AuthStore, app.state.auth)

    def refs(self) -> list[dict[str, str]]:
        out = []
        for e in self.registry.entries():
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

    def meta_json(self) -> str:
        apps = [{k: a[k] for k in ("color", "id", "ink", "name", "short")} for a in self.refs()]
        return dumps({"apps": apps, "permissions": list(PERMISSIONS)})

    def render(
        self, request: Request, name: str, eyebrow: str, title: str, subtitle: str, **ctx: Any
    ) -> Response:
        data = self.page_ctx(request) | {
            "icon": self.icon,
            "eyebrow": eyebrow,
            "title": title,
            "subtitle": subtitle,
        }
        return page_response(self.templates, request, name, data | ctx)

    async def known_roles(self) -> list[dict[str, Any]]:
        return await self.store.cfg.roles()

    async def locked_now(self, email: str) -> bool:
        limit = 5
        if self.auth.db:
            n = await self.auth.db.rate_like("login", f"acct:%|{email}", 15 * 60_000, now_ms())
            return n >= limit
        cut = now_ms() - 15 * 60_000
        return any(
            sum(1 for at in v if at > cut) >= limit
            for k, v in self.app.state.login_failures.items()
            if k.startswith("acct:") and k.endswith(f"|{email}")
        )

    async def view_of(self, u: dict[str, Any]) -> dict[str, Any]:
        return user_view(u, now_ms(), await self.locked_now(u["email"]))

    async def user(self, user_id: str) -> dict[str, Any] | None:
        async with self.auth.locked() as d:
            return next((dict(u) for u in d["users"] if u["id"] == user_id), None)

    async def users(self) -> list[dict[str, Any]]:
        async with self.auth.locked() as d:
            return [dict(u) for u in d["users"]]

    async def audit(self, request: Request, icon_: str, title: str, detail: str) -> None:
        await self.store.audit(actor_of(request), title, detail, icon=icon_)


def ctx(request: Request) -> AdminCtx:
    """FastAPI dependency: the Administration state that `mount_admin` stored on the app."""
    return cast(AdminCtx, request.app.state.admin_ctx)
