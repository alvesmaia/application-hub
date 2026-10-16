"""OAuth 2.1 for `/mcp` — `/oauth/authorize`: the consent screen and its decision."""

from __future__ import annotations

import secrets
from typing import Any
from urllib.parse import parse_qsl

from fastapi import Request
from fastapi.responses import Response

from hub.api.oauth_clients import OAuthClients
from hub.api.oauth_util import (
    CONSENT_PER_USER,
    CONSENT_TTL_MS,
    K_CODE,
    K_CONSENT,
    NO_ISSUER,
    OAuthError,
    canon,
    confusable,
    esc,
    form,
    host_of,
    now_ms,
    origin_of_url,
    redirect_matches,
    scope_label,
    sha,
    with_query,
)
from hub.api.v1 import P, principal


class OAuthAuthorize(OAuthClients):
    # ---- authorization ----------------------------------------------------------------------------
    def back_error(self, redirect_uri: str, iss: str, state: str | None, code: str, desc: str) -> Response:
        q = [("error", code), ("error_description", desc), ("iss", iss)]
        if state is not None:
            q.append(("state", state))
        return self.redirect(with_query(redirect_uri, q))

    async def authorize_get(self, request: Request) -> Response:
        q = form(request.url.query)
        target = await self.authorize_target(request, q)
        if isinstance(target, Response):
            return target
        iss, client, redirect_uri = target
        ours = self.authorize_params(request, q, iss, redirect_uri)
        if isinstance(ours, Response):
            return ours
        p = principal(request)
        if p is None or not p.session:
            return self.error_page(request, "Entre no hub com a sua conta para autorizar um aplicativo.")
        if not await self.hit(request.app, f"oauth-consent:{p.uid}", CONSENT_PER_USER):
            return self.error_page(
                request, "Pedidos de autorização demais em pouco tempo; tente de novo mais tarde."
            )
        req = {"client_id": client["id"], "client_name": client["name"], "client_kind": client["kind"],
               "redirect_uri": redirect_uri, "challenge": q.get("code_challenge", ""),
               "scopes": self.scopes_of(q.get("scope", "")), "resource": ours, "state": q.get("state"),
               "user": p.uid}  # fmt: skip
        nonce = secrets.token_hex(24)
        await self.mark_consent_pending(client["id"], now_ms() + CONSENT_TTL_MS)
        await self.rec.put(K_CONSENT, sha(nonce), req, now_ms() + CONSENT_TTL_MS)
        who = p.user.get("email") or p.name
        inner = self.consent_inner(request.app.title, who, req, nonce)
        return self.page_res(
            request,
            200,
            self.card(request, "Conectar um aplicativo ao hub", inner),
            origin_of_url(redirect_uri),
        )

    async def authorize_target(
        self, request: Request, q: dict[str, str]
    ) -> tuple[str, dict[str, Any], str] | Response:
        """`(issuer, client, redirect_uri)`; until the redirect is trusted, errors stay on the hub's own page."""
        if any(k.endswith("\0dup") for k in q):
            return self.error_page(request, "Parâmetro repetido no pedido de autorização.")
        if not (iss := self.issuer(request)):
            return self.error_page(request, NO_ISSUER)
        try:
            client = await self.load_client(q.get("client_id", ""))
        except OAuthError as e:
            return self.error_page(request, f"Cliente inválido: {e}.")
        uris = client["redirect_uris"]
        redirect_uri = q.get("redirect_uri") or (uris[0] if len(uris) == 1 else None)
        if redirect_uri is None:
            return self.error_page(request, "redirect_uri obrigatória.")
        if not any(redirect_matches(r, redirect_uri) for r in uris):
            return self.error_page(request, "redirect_uri não registrada para este cliente.")
        return iss, client, redirect_uri

    def authorize_params(
        self, request: Request, q: dict[str, str], iss: str, redirect_uri: str
    ) -> str | Response:
        """This hub's `/mcp` resource once response_type, PKCE and resource are valid (errors go back to the client)."""
        state = q.get("state")

        def back(code: str, desc: str) -> Response:
            return self.back_error(redirect_uri, iss, state, code, desc)

        if q.get("response_type") != "code":
            return back("unsupported_response_type", "Use response_type=code.")
        challenge = q.get("code_challenge", "")
        if q.get("code_challenge_method") != "S256" or not 43 <= len(challenge) <= 128:
            return back(
                "invalid_request", "PKCE obrigatório: code_challenge (S256) e code_challenge_method=S256."
            )
        if not (ours := self.resource(request)):
            return self.error_page(request, NO_ISSUER)
        if (r := q.get("resource")) is not None and canon(r) != canon(ours):
            return back("invalid_target", f"resource deve ser {ours} (o servidor MCP deste hub).")
        return ours

    def consent_inner(self, brand: str, who: str, r: dict[str, Any], nonce: str) -> str:
        kind = (f"Identificado pelo documento em <code>{esc(host_of(r['client_id']))}</code>" if r["client_kind"] == "cimd"
                else "Registrado automaticamente (registro dinâmico). O nome é declarado pelo próprio aplicativo")  # fmt: skip
        scopes = ""
        for s in r["scopes"]:
            ttl, desc = scope_label(s)
            control = ('<input type="checkbox" checked disabled/><input type="hidden" name="scope" value="mcp"/>' if s == "mcp"
                       else f'<input type="checkbox" name="scope" value="{esc(s)}" checked/>')  # fmt: skip
            scopes += (f'<li><label class="oauth-scope">{control} <span><strong>{esc(ttl)}</strong> <code>{esc(s)}</code><br/>'
                       f'<small class="ops-muted">{esc(desc)}</small></span></label></li>')  # fmt: skip
        warn = ('<strong data-oauth-confusable="">Atenção: o nome mistura letras de alfabetos diferentes (pode imitar outro aplicativo).</strong> Confira o endereço de retorno.'
                if confusable(r["client_name"])
                else "O hub não verifica quem é este aplicativo: confira o endereço de retorno antes de autorizar.")  # fmt: skip
        host, name = esc(host_of(r["redirect_uri"])), esc(r["client_name"])
        return (
            f'<p class="auth-copy"><span class="status-pill status-maintenance" data-oauth-unverified="">Não verificado</span> {warn}</p>'
            f'<p class="auth-copy">O aplicativo em <strong>{host}</strong> (que se apresenta como <strong data-oauth-client="">{name}</strong>) quer acessar o <strong>{esc(brand)}</strong> em seu nome (<strong>{esc(who)}</strong>).</p>\n'
            f'<dl class="oauth-facts">\n<dt>Aplicativo</dt><dd>{name} <small class="ops-muted">(nome declarado pelo aplicativo; confira o endereço abaixo)</small><br/><small class="ops-muted">{kind}</small></dd>\n'
            f'<dt>Volta para</dt><dd><code data-oauth-redirect-host="">{host}</code></dd>\n<dt>Acesso a</dt><dd><code>{esc(r["resource"])}</code></dd>\n</dl>\n'
            f'<form method="post" action="/oauth/authorize">\n<input type="hidden" name="consent" value="{nonce}"/>\n'
            f'<p class="auth-copy"><strong>Permissões pedidas</strong></p>\n<ul class="oauth-scopes" style="list-style:none;padding:0">{scopes}</ul>\n'
            f'<p class="ops-muted auth-note">O acesso vale até você revogar em <a href="/account/access#apps">Conta › Apps autorizados</a> (ou por {self.cfg.refresh_token_ttl_days} dias sem uso). Só autorize aplicativos em que você confia.</p>\n'
            '<div class="auth-actions"><button type="submit" name="decision" value="approve" class="primary-button" data-oauth-approve="">Autorizar</button> '
            '<button type="submit" name="decision" value="deny" class="secondary-button" data-oauth-deny="">Recusar</button></div>\n</form>'
        )  # fmt: skip

    async def authorize_post(self, request: Request, body: bytes) -> Response:
        text = body.decode("utf-8", "replace")
        f = form(text)
        if not (iss := self.issuer(request)):
            return self.error_page(request, NO_ISSUER)
        p = principal(request)
        if p is None or not p.session:
            return self.error_page(request, "Entre no hub com a sua conta para autorizar um aplicativo.")
        req = await self.rec.take(K_CONSENT, sha(f.get("consent", "")))
        if not req or req.get("user") != p.uid:
            return self.error_page(
                request,
                "Esta tela de autorização venceu ou já foi usada. Volte ao aplicativo e conecte de novo.",
            )
        try:
            client = await self.load_client(req["client_id"])
        except OAuthError as e:
            return self.error_page(request, f"Cliente inválido: {e}.")
        state = req.get("state")
        if f.get("decision") != "approve":
            await self.audit(
                p.name, "OAuth: autorização recusada", f"{req['client_name']} · {req['client_id']}"
            )
            return self.back_error(
                req["redirect_uri"], iss, state, "access_denied", "O usuário recusou o acesso."
            )
        return await self.approve_consent(p, req, client, text, iss)

    async def approve_consent(
        self, p: P, req: dict[str, Any], client: dict[str, Any], text: str, iss: str
    ) -> Response:
        """Consent approved: issue the one-time code (only the scopes still ticked) and send the user back."""
        state = req.get("state")
        chosen = [v for k, v in parse_qsl(text, keep_blank_values=True) if k == "scope"]
        scopes = [s for s in req["scopes"] if s == "mcp" or s in chosen]
        code, now = secrets.token_hex(32), now_ms()
        await self.rec.put(K_CODE, sha(code), {
            "client_id": req["client_id"], "client_name": req["client_name"], "user": p.uid,
            "redirect_uri": req["redirect_uri"], "challenge": req["challenge"], "scopes": scopes,
            "resource": req["resource"]}, now + self.cfg.code_ttl_s * 1000)  # fmt: skip
        if req["client_kind"] == "cimd":
            await self.remember_cimd(client)
        else:
            await self.mark_client_used(req["client_id"])
        await self.audit(p.name, "OAuth: acesso autorizado a aplicativo",
                         f"{req['client_name']} · {req['client_id']} · escopos: {' '.join(scopes)} · retorno: {host_of(req['redirect_uri'])}")  # fmt: skip
        q = [("code", code), ("iss", iss)]
        if state is not None:
            q.append(("state", state))
        return self.redirect(with_query(req["redirect_uri"], q))
