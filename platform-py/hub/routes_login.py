"""Login (password and Microsoft Entra ID), logout and the password change screen."""

from __future__ import annotations

import json
import os
import secrets
from contextlib import suppress
from typing import Any
from urllib.parse import parse_qs, quote, urlencode

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError

from hub.auth import (
    ABSOLUTE_MS,
    COOKIE,
    DUMMY_HASH,
    SSO_COOKIE,
    client_ip,
    entra,
    normalize_email,
    now_ms,
    positive_env,
    verify,
)
from hub.models import LoginInput
from hub.ux.account_routes import safe_next
from hub.webctx import WebCtx, public_base, set_session_cookie, sso_redirect_uri, web

router = APIRouter()
_WINDOW = 15 * 60_000


@router.get("/login", response_class=HTMLResponse)
async def login_get(request: Request, next: str = "/", w: WebCtx = Depends(web)) -> Response:
    if request.cookies.get(COOKIE) and await w.auth_store.session(request.cookies[COOKIE]):
        return RedirectResponse(safe_next(next), status_code=303)
    return w.login_page(request, next)


@router.get("/auth/entra/login")
async def entra_login(request: Request, next: str = "/", w: WebCtx = Depends(web)) -> Response:
    if not (sso := w.sso):
        return RedirectResponse("/login", status_code=303)
    url, pending = sso.authorize_url(sso_redirect_uri(request))
    resp = RedirectResponse(url, status_code=303)
    resp.set_cookie(
        SSO_COOKIE,
        json.dumps({**pending, "next": safe_next(next)}, separators=(",", ":")),
        max_age=600,
        path="/auth/entra/",
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https" or os.getenv("HUB_PUBLIC_URL", "").startswith("https://"),
    )
    return resp


@router.get("/auth/entra/callback")
async def entra_callback(request: Request, w: WebCtx = Depends(web)) -> Response:
    if not (sso := w.sso):
        return RedirectResponse("/login", status_code=303)
    try:
        pending = json.loads(request.cookies.get(SSO_COOKIE, "{}"))
    except ValueError:
        pending = {}
    next_url = safe_next(str(pending.get("next", "/")))
    admin_store = getattr(request.app.state, "admin", None)
    ip = client_ip(request)
    error = ""
    user: dict[str, Any] | None = None
    try:
        user = await _sso_user(request, w, sso, pending, admin_store)
    except (entra.SsoError, httpx.HTTPError, KeyError, ValueError) as e:
        error = (
            str(e) if isinstance(e, entra.SsoError) else "Não foi possível concluir o login com a Microsoft."
        )
    if user is None:
        if admin_store:
            await admin_store.audit("SSO", "Login SSO recusado", f"{error} · via {ip}", icon="users")
        resp = w.login_page(request, next_url, error, status=401)
        resp.delete_cookie(SSO_COOKIE, path="/auth/entra/")
        return resp
    if admin_store:
        await admin_store.audit(
            user["name"], "Acesso à plataforma", f"Login SSO (Entra ID) via {ip}", icon="users"
        )
    w.insight.analytics.record(user["id"], "login")
    cookie = await w.auth_store.session_new(user, request.cookies.get(COOKIE), method="entra")
    resp = RedirectResponse(next_url, status_code=303)
    set_session_cookie(request, resp, cookie)
    resp.delete_cookie(SSO_COOKIE, path="/auth/entra/")
    return resp


async def _sso_user(
    request: Request, w: WebCtx, sso: entra.EntraConfig, pending: dict[str, Any], admin_store: Any
) -> dict[str, Any]:
    """Validates the Entra ID answer and returns the hub user (created on first login); `SsoError` says why not."""
    q = request.query_params
    if q.get("error"):
        raise entra.SsoError("O Microsoft Entra ID recusou o login.")
    state = q.get("state", "")
    if not state or not secrets.compare_digest(state, str(pending.get("state", ""))):
        raise entra.SsoError("Sessão de login expirada; tente de novo.")
    id_token = await sso.exchange(q.get("code", ""), str(pending["verifier"]), sso_redirect_uri(request))
    claims = await sso.validate(id_token, str(pending.get("nonce", "")))
    raw_email = str(claims.get("email") or claims.get("preferred_username") or "")
    try:
        email = normalize_email(raw_email)
    except ValueError as e:
        raise entra.SsoError("A conta Microsoft não informou um e-mail válido.") from e
    if not sso.domain_ok(email):
        raise entra.SsoError("Este domínio de e-mail não tem acesso à plataforma.")
    user, created = await w.auth_store.sso_login(
        str(claims["tid"]),
        str(claims.get("oid") or claims["sub"]),
        email,
        str(claims.get("name") or ""),
        sso.roles_for(claims),
        sso.default_role,
    )
    if user is None:
        raise entra.SsoError("Usuário desativado. Fale com o administrador.")
    if admin_store and created:
        await admin_store.audit(user["name"], "Usuário criado pelo SSO", f"{email} (Entra ID)", icon="users")
    return user


def _cross_site(request: Request) -> bool:
    fetch_site = request.headers.get("sec-fetch-site")
    return bool(
        fetch_site == "cross-site"
        or (not request.headers.get("origin") and fetch_site not in ("same-origin", "same-site", "none"))
        or (
            request.headers.get("origin")
            and request.headers["origin"].split("://", 1)[-1] != request.headers.get("host")
        )
    )


@router.post("/login")
async def login_post(request: Request, w: WebCtx = Depends(web)) -> Response:
    form = parse_qs((await request.body()).decode("utf-8", "replace"))
    try:
        login = LoginInput.model_validate(
            {"email": form.get("email", [""])[0], "password": form.get("password", [""])[0]}
        )
    except ValidationError:
        return Response("Dados de acesso inválidos.", status_code=400)
    email, password = login.email.strip().lower(), login.password
    next_url = form.get("next", ["/"])[0]
    if _cross_site(request):
        return Response("Requisição bloqueada.", status_code=403)
    ip = client_ip(request)
    limits = (positive_env("HUB_LOGIN_RATE_ACCOUNT", 5, 1000), positive_env("HUB_LOGIN_RATE_IP", 30, 10_000))
    buckets = (f"acct:{ip}|{email}", f"ip:{ip}")
    try:
        counts = await _failures(request, w, buckets)
    except Exception:
        return Response("State database unavailable", status_code=503)
    if any(count >= limit for count, limit in zip(counts, limits, strict=True)):
        verify(password, DUMMY_HASH)
        error, status = "Muitas tentativas de acesso. Aguarde alguns minutos e tente novamente.", 429
        return w.login_page(request, next_url, error, email, status)
    user = await w.auth_store.authenticate(email, password)
    if user and (
        w.local_mode == "off" or (w.local_mode == "admins" and "Administrador" not in user["roles"])
    ):
        user = None
    if user:
        return await _signed_in(request, w, user, next_url, ip)
    await _count_failure(request, w, buckets, limits)
    return w.login_page(request, next_url, "E-mail ou senha incorretos.", email, 401)


async def _failures(request: Request, w: WebCtx, buckets: tuple[str, str]) -> list[int]:
    """Failed logins in the current window, per account and per IP (state database, or memory without one)."""
    db = w.auth_store.db
    if db:
        return [await db.rate_peek("login", key, _WINDOW, now_ms()) for key in buckets]
    failures = request.app.state.login_failures
    return [len([at for at in failures.get(key, []) if at > now_ms() - _WINDOW]) for key in buckets]


async def _count_failure(
    request: Request, w: WebCtx, buckets: tuple[str, str], limits: tuple[int, int]
) -> None:
    db = w.auth_store.db
    if db:
        for key, limit in zip(buckets, limits, strict=True):
            await db.rate_hit("login", key, _WINDOW, limit, now_ms())
    else:
        for key in buckets:
            request.app.state.login_failures.setdefault(key, []).append(now_ms())


async def _signed_in(request: Request, w: WebCtx, user: dict[str, Any], next_url: str, ip: str) -> Response:
    if admin_store := getattr(request.app.state, "admin", None):
        await admin_store.audit(user["name"], "Acesso à plataforma", f"Login via {ip}", icon="users")
    w.insight.analytics.record(user["id"], "login")
    cookie = await w.auth_store.session_new(user, request.cookies.get(COOKIE))
    target = next_url if next_url.startswith("/") and not next_url.startswith(("//", "/\\")) else "/"
    if user.get("must_change_password"):
        target = "/account/password?next=" + quote(target, safe="/")
    resp = RedirectResponse(target, status_code=303)
    set_session_cookie(request, resp, cookie)
    with suppress(OSError):
        (w.data_dir / "bootstrap-admin.txt").unlink()
    return resp


@router.post("/logout")
async def logout(request: Request, w: WebCtx = Depends(web)) -> Response:
    if value := request.cookies.get(COOKIE):
        if admin_store := getattr(request.app.state, "admin", None):
            await admin_store.audit(
                request.state.user["name"],
                "Sessão encerrada",
                f"Logout via {client_ip(request)}",
                icon="users",
            )
        method = await w.auth_store.logout(value)
    else:
        method = ""
    target = "/login?m=saiu"
    if method == "entra" and (sso := request.app.state.sso):
        # Single logout: Microsoft ends its session and returns to the hub login.
        target = (
            sso.endpoint("logout", back=False)
            + "?"
            + urlencode({"post_logout_redirect_uri": public_base(request) + "/login"})
        )
    resp = RedirectResponse(target, status_code=303)
    resp.delete_cookie(COOKIE, path="/")
    return resp


@router.get("/account/password", response_class=HTMLResponse)
async def password_get(request: Request, next: str = "/", w: WebCtx = Depends(web)) -> Response:
    return w.password_page(request, next_url=safe_next(next))


@router.post("/account/password")
async def password_post(request: Request, w: WebCtx = Depends(web)) -> Response:
    user = request.state.user
    f = parse_qs((await request.body()).decode("utf-8", "replace"))
    current, new = f.get("current", [""])[0], f.get("new", [""])[0]
    target = safe_next(f.get("next", ["/"])[0])
    if new != f.get("confirm", [""])[0]:
        return w.password_page(request, "As senhas não coincidem.", target, 400)
    try:
        if not await w.auth_store.change_password(user, current, new):
            return w.password_page(request, "Senha atual incorreta.", target, 400)
    except ValueError as e:
        return w.password_page(request, str(e), target, 400)
    # The previous sessions were dropped; this one continues with a new id and returns to where the person was.
    resp = RedirectResponse(target, status_code=303)
    resp.set_cookie(
        COOKIE,
        await w.auth_store.session_new(user),
        max_age=ABSOLUTE_MS // 1000,
        path="/",
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    return resp
