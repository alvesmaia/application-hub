"""`hub login`: OAuth 2.0 device authorization (RFC 8628) for the built-in `hub-cli` client.

The CLI asks for a code, the person approves it in the browser (signed in to the hub) and the CLI polls `/oauth/token`
until it gets its tokens. One approval covers every surface the CLI uses (API v1, deploy, MCP, app APIs), always
with the person's own permissions; the session ends after 30 days unused or 90 days in total."""

from __future__ import annotations

import secrets
from typing import Any, override

from fastapi import Request
from fastapi.responses import Response

from hub.api.oauth_tokens import OAuthTokens
from hub.api.oauth_util import (
    CLI_CLIENT,
    CLI_NAME,
    CLI_SCOPES,
    CONSENT_PER_USER,
    DAY_MS,
    DEVICE_GRANT,
    K_GRANT,
    NO_ISSUER,
    cli_client,
    esc,
    form,
    grant_record,
    now_ms,
    sha,
)
from hub.api.v1 import principal
from hub.state import Change

K_DEVICE, K_USER_CODE = "oauth.device", "oauth.usercode"
DEVICE_TTL_S, POLL_S = 600, 5
_ALPHABET = "BCDFGHJKLMNPQRSTVWXZ"  # no vowels (no words), no look-alike digits: 20^8 codes, rate limited


def user_code() -> str:
    raw = "".join(secrets.choice(_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def norm_code(s: str) -> str:
    raw = "".join(c for c in s.upper() if c.isalpha())
    return f"{raw[:4]}-{raw[4:]}" if len(raw) == 8 else ""


class OAuthDevice(OAuthTokens):
    async def device_authorization(self, request: Request, body: bytes) -> Response:
        f = form(body.decode("utf-8", "replace"))
        if f.get("client_id") != CLI_CLIENT:
            return self.oerr(401, "invalid_client", f"Só o cliente {CLI_CLIENT} usa o login por código.")
        if not (iss := self.issuer(request)):
            return self.oerr(400, "invalid_request", NO_ISSUER)
        device, code, now = secrets.token_urlsafe(32), user_code(), now_ms()
        exp = now + DEVICE_TTL_S * 1000
        await self.rec.put(K_DEVICE, sha(device), {"user_code": code, "status": "pending", "exp": exp}, exp)
        await self.rec.put(K_USER_CODE, code, {"device": sha(device)}, exp)
        uri = f"{iss}/oauth/device"
        return self.json_res(200, {"device_code": device, "user_code": code, "verification_uri": uri,
                                   "verification_uri_complete": f"{uri}?user_code={code}",
                                   "expires_in": DEVICE_TTL_S, "interval": POLL_S})  # fmt: skip

    @override
    async def device_grant(self, f: dict[str, str], client: dict[str, Any], ours: str) -> Response:
        """Token endpoint, device grant: pending / slow_down / denied / expired, or the tokens once approved."""
        key, now = sha(f.get("device_code", "")), now_ms()
        if client["id"] != CLI_CLIENT:
            return self.oerr(400, "unauthorized_client", "Login por código só para o Hub CLI.")
        last = self.device_last_poll.get(key, 0)
        self.device_last_poll[key] = now
        d = await self.rec.update(K_DEVICE, key, _claim_device)
        if d is None or d["exp"] <= now:
            return self.oerr(400, "expired_token", "Código vencido: rode `hub login` de novo.")
        if d["status"] == "pending":
            slow = now - last < POLL_S * 1000
            return self.oerr(400, "slow_down" if slow else "authorization_pending", "Aguardando a aprovação.")
        if d["status"] == "denied":
            return self.oerr(400, "access_denied", "O acesso foi recusado no navegador.")
        if d["status"] != "claimed":
            return self.oerr(400, "invalid_grant", "Código já usado.")
        self.device_last_poll.pop(key, None)
        return await self.device_issue(d["user"], ours, now)

    async def device_issue(self, user: str, ours: str, now: int) -> Response:
        if not await self.user_ok(user):
            return self.oerr(400, "invalid_grant", "Conta desativada.")
        gid = f"g{secrets.token_hex(12)}"
        grant = grant_record(gid, cli_client(), user, CLI_SCOPES, ours, now)
        await self.rec.put(K_GRANT, gid, grant, now + self.cfg.refresh_token_ttl_days * DAY_MS)
        await self.audit(await self.user_label(user), "OAuth: token emitido (login do CLI)",
                         f"{CLI_NAME} · escopos: {' '.join(CLI_SCOPES)}")  # fmt: skip
        return self.json_res(200, await self.issue(grant))

    # ---- the page where the person approves the code ----------------------------------------------
    async def device_get(self, request: Request) -> Response:
        p = principal(request)
        if p is None or not p.session:
            return self.error_page(request, "Entre no hub com a sua conta para autorizar o Hub CLI.")
        code = norm_code(request.query_params.get("user_code", ""))
        who = p.user.get("email") or p.name
        return self.page_res(request, 200, self.card(request, "Autorizar o Hub CLI", device_inner(code, who),
                                                     "Login do CLI"))  # fmt: skip

    async def device_post(self, request: Request, body: bytes) -> Response:
        p = principal(request)
        if p is None or not p.session:
            return self.error_page(request, "Entre no hub com a sua conta para autorizar o Hub CLI.")
        if not await self.hit(request.app, f"oauth-device:{p.uid}", CONSENT_PER_USER):
            return self.error_page(request, "Tentativas demais em pouco tempo; tente de novo mais tarde.")
        f = form(body.decode("utf-8", "replace"))
        code, approve = norm_code(f.get("user_code", "")), f.get("decision") == "approve"
        link = await self.rec.get(K_USER_CODE, code) if code else None
        decided = link and await self.rec.update(
            K_DEVICE, link["device"], lambda cur: _decide(cur, approve, p.uid)
        )
        if not decided:
            return self.device_done(
                request, 400, "Código não encontrado ou vencido. Rode `hub login` de novo."
            )
        await self.rec.delete(K_USER_CODE, code)
        title = "OAuth: login do CLI autorizado" if approve else "OAuth: login do CLI recusado"
        await self.audit(p.name, title, f"{CLI_NAME} · código {code}")
        msg = ("Pronto: o Hub CLI está conectado à sua conta. Pode voltar ao terminal." if approve
               else "Acesso recusado. Nada foi autorizado.")  # fmt: skip
        return self.device_done(request, 200, msg)

    def device_done(self, request: Request, status: int, msg: str) -> Response:
        inner = (f'<p class="auth-copy" data-oauth-device-result="">{esc(msg)}</p>'
                 '<div class="auth-actions"><a class="secondary-button auth-link" href="/">Ir para o início</a></div>')  # fmt: skip
        return self.page_res(request, status, self.card(request, "Login do CLI", inner, "Login do CLI"))


def device_inner(code: str, who: str) -> str:
    return (
        f'<p class="auth-copy">O <strong>{CLI_NAME}</strong> (no seu terminal ou usado pelo seu agente de IA) vai agir em seu nome '
        f'(<strong>{esc(who)}</strong>), com as suas permissões: implantar, usar a API, o MCP e as APIs dos apps.</p>'
        '<form method="post" action="/oauth/device">'
        '<label class="auth-label" for="user_code">Código mostrado no terminal</label>'
        f'<input class="auth-input" id="user_code" name="user_code" value="{esc(code)}" autocomplete="off" '
        'autocapitalize="characters" spellcheck="false" required placeholder="XXXX-XXXX"/>'
        '<p class="ops-muted auth-note">Confira se é o mesmo código do terminal. O login vale por 30 dias sem uso e no '
        'máximo 90 dias; revogue quando quiser em <a href="/account/access#apps">Minha conta › Acessos</a>.</p>'
        '<div class="auth-actions"><button type="submit" name="decision" value="approve" class="primary-button" '
        'data-oauth-approve="">Autorizar</button> <button type="submit" name="decision" value="deny" '
        'class="secondary-button" data-oauth-deny="">Recusar</button></div></form>'
    )  # fmt: skip


def _claim_device(cur: Any) -> tuple[Change, Any]:
    """Approved → claimed exactly once (the poll that sees it gets the tokens); anything else is just read."""
    if cur and cur.get("status") == "approved":
        cur = cur | {"status": "claimed"}
        return Change.set(cur | {"status": "used"}, cur["exp"]), cur
    return Change.keep(), cur


def _decide(cur: Any, approve: bool, uid: str) -> tuple[Change, bool]:
    if not cur or cur.get("status") != "pending" or cur["exp"] <= now_ms():
        return Change.keep(), False
    status = {"status": "approved", "user": uid} if approve else {"status": "denied"}
    return Change.set(cur | status, cur["exp"]), True


__all__ = ["CLI_CLIENT", "CLI_SCOPES", "DEVICE_GRANT", "OAuthDevice"]
