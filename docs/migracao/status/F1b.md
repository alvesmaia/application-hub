# F1b — Gateway (status)

Feito em `platform-py/hub/gateway/` (httpx streaming + Starlette WebSocket + `websockets`):
- `identity.py`: `x-hub-user` (JSON compacto, base64url) e `x-hub-user-sig` idênticos ao Rust (HMAC por app, `v1.<ts>.<hex>`, chave `HUB_USER_SIG_KEY`); vetores golden calculados com openssl. Identidade = admin fixo (TODO F1 auth).
- `registry.py`: `HUB_APPS_FILE` (apps.json / apps.compose.json), limites de upload, `forward_auth_schemes`.
- `proxy.py`: `/_frag/{id}/...` e `/apps/{id}/api/*` -> `{upstream}/{id}/...`; hop-by-hop, `x-hub-*` do cliente descartados, cookies `hub_*`/Authorization filtrados, Set-Cookie sanitizado, Location -> `/apps/{id}`, `x-hub-base/env/locale`, CSRF same-origin, `_hub` e `%25` -> 404, 413 por app, streaming nos dois sentidos, prazo de resposta (504 `data-hub-unavailable` + Retry-After), ociosidade, 502 se o app cai.
- `ws.py`: `/apps/{id}/ws[/...]` (Origin = Host, 8 sockets por usuário/app, mensagem 256 KiB, ocioso 60 s, vida 900 s -> 1012; `HUB_WS_*`).
- `csp.py`: `csp_with` (hashes de scripts aprovados, Trusted Types, form-action, connect ws/wss, `sandbox` em `/_frag`). `approved_scripts` = stub vazio (F3).
- `routes.py`: página `/apps/{id}[/{secao}]` (template `app.html`, hub.js inalterado), redirecionamento 301/308 `/{id}/...` -> `/apps/{id}/...` (middleware).
- Compose `hubpy`: `HUB_APPS_FILE` + redes externas `hub-app-<id>` (somente uso; pilha Rust intocada).
Testes: `tests/test_gateway.py` (20 casos; upstream ASGI em memória + uvicorn real para WS).

Verificação ao vivo (hubpy :8090 -> apps da pilha Rust): `/apps/ativos|coletor|contratos` renderizam no shell Python sem erro de console; `/ativos` -> 301; WS `ws://localhost:8090/apps/coletor/ws/live` entrega mensagens; Origin alheio -> 403.

Lacunas: sem lease/drenagem blue/green; staging (`hub_env`) e `RequestedEnv` ausentes; telemetria/traceparent; `/apps/{id}/api` sem token de API (F1 auth); `x-hub-locale` vazio (sem sessão); botão do rail no modo app não replica o rótulo do Rust; `ruff`/`check.sh` global falha em `tests/test_vault.py` e `hub/state` (Codex, fora do escopo).
