"""OAuth 2.1 for `/mcp` — `/oauth/token` (code and refresh grants), revocation and access-token checks."""

from __future__ import annotations

import base64
import secrets
from collections.abc import Callable
from functools import partial
from typing import Any
from urllib.parse import unquote_plus

from fastapi import Request
from fastapi.responses import Response

from hub.api.oauth_authorize import OAuthAuthorize
from hub.api.oauth_util import (
    DAY_MS,
    K_ACCESS,
    K_CODE,
    K_GRANT,
    K_REFRESH,
    REVOKE_PER_IP,
    TOKEN_PER_IP,
    OAuthError,
    canon,
    form,
    now_ms,
    pkce_ok,
    sha,
)
from hub.auth import client_ip
from hub.state import Change


class OAuthTokens(OAuthAuthorize):
    # ---- token -------------------------------------------------------------------------------------
    def client_auth(self, request: Request, f: dict[str, str]) -> tuple[str, str | None] | Response:
        def invalid() -> Response:
            return self.oerr(
                401,
                "invalid_client",
                "Autenticação do cliente inválida.",
                {"www-authenticate": 'Basic realm="hub-oauth"'},
            )

        if a := request.headers.get("authorization"):
            if not a.lower().startswith("basic "):
                return invalid()
            try:
                s = base64.b64decode(a[6:].strip(), validate=True).decode()
            except ValueError:
                return invalid()
            cid, sep, sec = s.partition(":")
            if not sep:
                return invalid()
            return unquote_plus(cid), unquote_plus(sec)
        if "client_id" not in f:
            return invalid()
        return f["client_id"], f.get("client_secret")

    async def authenticate_client(self, request: Request, f: dict[str, str]) -> dict[str, Any] | Response:
        ca = self.client_auth(request, f)
        if isinstance(ca, Response):
            return ca
        cid, secret = ca
        try:
            c = await self.load_client(cid)
        except OAuthError as e:
            return self.oerr(401, "invalid_client", str(e))
        if c["secret_hash"] is None:
            if c["auth_method"] == "none":
                return c
            return self.oerr(401, "invalid_client", "Cliente confidencial sem segredo registrado.")
        if secret is not None and secrets.compare_digest(c["secret_hash"], sha(secret)):
            return c
        return self.oerr(401, "invalid_client", "client_secret ausente ou inválido.")

    async def issue(self, grant: dict[str, Any]) -> dict[str, Any]:
        c, now = self.cfg, now_ms()
        access, refresh = f"hubo_{secrets.token_hex(32)}", f"hubr_{secrets.token_hex(32)}"
        gid, exp = grant["grant_id"], now + c.access_token_ttl_s * 1000
        await self.rec.put(K_ACCESS, sha(access), {
            "grant_id": gid, "user": grant["user"], "client_id": grant["client_id"],
            "client_name": grant["client_name"], "scopes": grant["scopes"], "aud": grant["resource"],
            "exp": exp, "created_at": now}, exp)  # fmt: skip
        await self.rec.put(
            K_REFRESH, sha(refresh), {"grant_id": gid, "used": False}, now + c.refresh_token_ttl_days * DAY_MS
        )
        return {"access_token": access, "token_type": "Bearer", "expires_in": c.access_token_ttl_s,
                "refresh_token": refresh, "scope": " ".join(grant["scopes"])}  # fmt: skip

    async def revoke_grant(self, gid: str) -> dict[str, Any] | None:
        g = await self.rec.take(K_GRANT, gid)
        await self.rec.del_where(K_ACCESS, "grant_id", gid)
        await self.rec.del_where(K_REFRESH, "grant_id", gid)
        return g  # type: ignore[no-any-return]

    async def token(self, request: Request, body: bytes) -> Response:
        app, now = request.app, now_ms()
        if not await self.hit(app, f"oauth-token:{client_ip(request)}", TOKEN_PER_IP):
            return self.oerr(429, "slow_down", "Pedidos demais; tente mais tarde.")
        f = form(body.decode("utf-8", "replace"))
        if any(k.endswith("\0dup") for k in f):
            return self.oerr(400, "invalid_request", "Parâmetro repetido.")
        client = await self.authenticate_client(request, f)
        if isinstance(client, Response):
            return client
        if not (ours := self.resource(request)):
            return self.no_issuer()
        if (r := f.get("resource")) is not None and canon(r) != canon(ours):
            return self.oerr(400, "invalid_target", f"resource deve ser {ours}.")

        def grant_err(d: str) -> Response:
            return self.oerr(400, "invalid_grant", d)

        gt = f.get("grant_type")
        if gt == "authorization_code":
            return await self.code_grant(f, client, ours, now, grant_err)
        if gt == "refresh_token":
            return await self.refresh_grant(f, client, now, grant_err)
        if gt is not None:
            return self.oerr(
                400, "unsupported_grant_type", "grant_type: authorization_code ou refresh_token."
            )
        return self.oerr(400, "invalid_request", "grant_type obrigatório.")

    async def code_grant(
        self,
        f: dict[str, str],
        client: dict[str, Any],
        ours: str,
        now: int,
        grant_err: Callable[[str], Response],
    ) -> Response:
        code = f.get("code", "")
        key, redirect_uri, verifier = sha(code), f.get("redirect_uri"), f.get("code_verifier", "")
        gid = f"g{secrets.token_hex(12)}"
        consume = partial(_consume_code, client_id=client["id"], redirect_uri=redirect_uri, verifier=verifier,
                          ours=ours, gid=gid, now=now)  # fmt: skip
        kind, info = await self.rec.update(K_CODE, key, consume)
        if kind != "fresh":
            return await self.code_refused(kind, info, client, grant_err)
        return await self.code_issue(key, gid, info, client, now, grant_err)

    async def code_refused(
        self, kind: str, info: Any, client: dict[str, Any], grant_err: Callable[[str], Response]
    ) -> Response:
        if kind == "missing":
            return grant_err("Código inválido, vencido ou já usado.")
        if kind == "replay":
            if g := await self.revoke_grant(info):
                await self.audit("OAuth", "OAuth: código de autorização reutilizado — concessão revogada",
                                 f"{g.get('client_name', '')} · usuário {await self.user_label(g.get('user', ''))}")  # fmt: skip
            else:
                await self.audit(
                    "OAuth", "OAuth: código de autorização reutilizado", f"{client['name']} · {client['id']}"
                )
            return grant_err("Código inválido, vencido ou já usado.")
        why, c = info  # kind == "rejected"
        await self.audit(await self.user_label(c.get("user", "")), "OAuth: troca de código recusada",
                         f"{client['name']} · {client['id']} · {why}")  # fmt: skip
        return grant_err(why)

    async def code_issue(
        self,
        key: str,
        gid: str,
        c: dict[str, Any],
        client: dict[str, Any],
        now: int,
        grant_err: Callable[[str], Response],
    ) -> Response:
        user = c["user"]
        if not await self.user_ok(user):
            return grant_err("Conta desativada.")
        grant = {"grant_id": gid, "client_id": client["id"], "client_name": c["client_name"], "client_kind": client["kind"],
                 "user": user, "scopes": c["scopes"], "resource": c["resource"], "redirect_uri": c["redirect_uri"],
                 "created_at": now, "last_used": now}  # fmt: skip
        await self.rec.put(K_GRANT, gid, grant, now + self.cfg.refresh_token_ttl_days * DAY_MS)
        # A replay that raced this exchange marked the code after we consumed it: drop the grant we just made.
        cur = await self.rec.get(K_CODE, key)
        if cur and cur.get("replayed") is True:
            await self.revoke_grant(gid)
            return grant_err("Código reutilizado: a concessão foi revogada por segurança.")
        await self.audit(await self.user_label(user), "OAuth: token emitido (código de autorização)",
                         f"{client['name']} · {client['id']} · escopos: {' '.join(grant['scopes'])}")  # fmt: skip
        return self.json_res(200, await self.issue(grant))

    async def refresh_grant(
        self, f: dict[str, str], client: dict[str, Any], now: int, grant_err: Callable[[str], Response]
    ) -> Response:
        key = sha(f.get("refresh_token", ""))
        ttl = now + self.cfg.refresh_token_ttl_days * DAY_MS
        r = await self.rec.update(K_REFRESH, key, partial(_rotate_refresh, ttl=ttl))
        if r is None:
            return grant_err("Refresh token inválido, vencido ou revogado.")
        gid, reused = r
        if reused:
            return await self.refresh_reused(gid, grant_err)
        touch = partial(_touch_grant, client_id=client["id"], now=now, ttl=ttl)
        grant = await self.rec.update(K_GRANT, gid, touch)
        if isinstance(grant, str):
            return grant_err(grant)
        if not await self.user_ok(grant["user"]):
            await self.revoke_grant(gid)
            return grant_err("Conta desativada.")
        if "scope" in f:
            want = self.scopes_of(f["scope"])
            if any(w not in grant["scopes"] for w in want):
                return self.oerr(400, "invalid_scope", "Escopo maior que o concedido.")
            grant = grant | {"scopes": want}
        return self.json_res(200, await self.issue(grant))

    async def refresh_reused(self, gid: str, grant_err: Callable[[str], Response]) -> Response:
        if g := await self.revoke_grant(gid):
            await self.audit("OAuth", "OAuth: refresh token reutilizado — concessão revogada",
                             f"{g.get('client_name', '')} · usuário {await self.user_label(g.get('user', ''))} (possível roubo de token)")  # fmt: skip
            self.notify_reuse(g)
        return grant_err("Refresh token já usado: a concessão foi revogada por segurança.")

    def notify_reuse(self, g: dict[str, Any]) -> None:
        from hub.ux.inbox import item_of

        self.inbox.push(item_of(
            "notification", "platform", f'Acesso de "{g.get("client_name") or "aplicativo"}" revogado por segurança',
            body="Um refresh token deste aplicativo foi usado duas vezes (possível vazamento). O hub revogou o acesso; conecte o aplicativo de novo se foi você.",
            link="/account/access#apps", level="warning", at=now_ms(), source=f"oauth-reuse:{g.get('grant_id', '')}",
            to_users=[g["user"]]))  # fmt: skip

    async def revoke(self, request: Request, body: bytes) -> Response:
        if not await self.hit(request.app, f"oauth-revoke:{client_ip(request)}", REVOKE_PER_IP):
            return self.oerr(429, "slow_down", "Pedidos demais; tente mais tarde.")
        f = form(body.decode("utf-8", "replace"))
        client = await self.authenticate_client(request, f)
        if isinstance(client, Response):
            return client
        tok = f.get("token", "")
        key = sha(tok)
        if tok.startswith("hubr_"):
            r = await self.rec.get(K_REFRESH, key)
            owned = r and (g := await self.rec.get(K_GRANT, r["grant_id"])) and g["client_id"] == client["id"]
            if owned and r and (g := await self.revoke_grant(r["grant_id"])):
                await self.audit(
                    await self.user_label(g["user"]),
                    "OAuth: acesso revogado pelo aplicativo",
                    f"{client['name']} · {client['id']}",
                )
        elif (a := await self.rec.get(K_ACCESS, key)) and a["client_id"] == client["id"]:
            await self.rec.delete(K_ACCESS, key)
        return self.json_res(200, {})  # RFC 7009: 200 even for an unknown token

    # ---- resource server ---------------------------------------------------------------------------
    async def verify_access(self, tok: str, request: Request) -> dict[str, Any] | None:
        """OAuth token (`hubo_…`) valid for this `/mcp`: returns a synthetic token with an owner."""
        resource = self.resource(request)
        if not self.on or not tok.startswith("hubo_"):
            return None
        a = await self.rec.get(K_ACCESS, sha(tok))
        now = now_ms()
        if not a or not resource or canon(a["aud"]) != canon(resource) or a["exp"] <= now:
            return None
        gid = a["grant_id"]
        g = await self.rec.get(K_GRANT, gid)
        if not g:
            return None
        if now - g.get("last_used", 0) > 60_000:
            await self.rec.update(K_GRANT, gid, lambda cur: (
                Change.set(cur | {"last_used": now}, now + self.cfg.refresh_token_ttl_days * DAY_MS) if cur else Change.keep(), None))  # fmt: skip
        return {"id": f"oauth-{gid}", "name": f"OAuth: {a.get('client_name') or 'aplicativo'}", "hash": "",
                "prefix": "hubo_", "apps": [], "envs": [], "actions": list(a["scopes"]), "created_by": a["client_id"],
                "created_at": a.get("created_at", now), "expires_at": a["exp"], "revoked_at": None,
                "last_used": now, "owner": a["user"]}  # fmt: skip


# ---- atomic record updates (run inside `rec.update`, so they only compute the change) ------------------
def _consume_code(
    cur: Any, *, client_id: str, redirect_uri: str | None, verifier: str, ours: str, gid: str, now: int
) -> tuple[Change, tuple[str, Any]]:
    if cur is None:
        return Change.keep(), ("missing", None)
    if g := cur.get("used_grant"):
        return Change.set(cur | {"replayed": True}, now + 10 * 60_000), ("replay", g)
    bad = None
    if cur["client_id"] != client_id:
        bad = "Código emitido para outro cliente."
    elif redirect_uri != cur["redirect_uri"]:
        bad = "redirect_uri diferente da usada na autorização."
    elif not pkce_ok(verifier, cur["challenge"]):
        bad = "code_verifier inválido (PKCE)."
    elif canon(cur["resource"]) != canon(ours):
        bad = "resource diferente do autorizado."
    if bad:
        return Change.keep(), ("rejected", (bad, cur))
    return Change.set(cur | {"used_grant": gid}, now + 10 * 60_000), ("fresh", cur)


def _rotate_refresh(cur: Any, *, ttl: int) -> tuple[Change, tuple[str, bool] | None]:
    if cur is None:
        return Change.keep(), None
    if cur.get("used") is True:
        return Change.keep(), (str(cur["grant_id"]), True)
    return Change.set(cur | {"used": True}, ttl), (str(cur["grant_id"]), False)


def _touch_grant(cur: Any, *, client_id: str, now: int, ttl: int) -> tuple[Change, Any]:
    if cur is None:
        return Change.keep(), "Concessão revogada."
    if cur["client_id"] != client_id:
        return Change.keep(), "Refresh token de outro cliente."
    cur = cur | {"last_used": now}
    return Change.set(cur, ttl), cur
