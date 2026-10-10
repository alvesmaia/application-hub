# F2b - Busca, Inbox, Configuracoes, i18n

Codigo em `platform-py/hub/ux/`: `search.py`, `inbox.py` (store + regras), `inbox_routes.py` (paginas `/inbox` `/search`, `/api/me/inbox*`, `/api/me/search` (+ alias `/api/search`), SSE `/api/me/stream`, `/api/me/access-requests`, `/api/admin/grants/revoke`), `account_routes.py` (`/account/settings|tokens|authorized-apps`, `POST /account/prefs`, `/account/agent-approval/{id}`), `shell.py` (menu/JS/paleta por idioma, tema), `texts.py` (`resolve`, `t/tn`, `tr` por `web/catalog.json`).

- Busca: plataforma (apps, paginas, menu de Admin por permissao) + apps em paralelo (`GET {upstream}/{id}/_hub/search`, token interno + `x-hub-user`, timeout `HUB_SEARCH_TIMEOUT_MS` padrao 1,5 s, 64 KiB, `url` fora do app descartado, 404 = app sem busca por 5 min), apps primeiro, grupo interno (usuarios, implantacoes, variaveis) so com permissao, destaque `<mark>`, "ver todos" por app (`&app=`), balde de 20 fichas + 4/s por usuario. Token interno por env `HUB_INTERNAL_TOKEN_<APP>` (o cofre de variaveis e F3; dev: `.dev.env`).
- Inbox: `inbox.json` (itens, estado lido/arquivado por usuario, concessoes). Notificacoes, aprovacoes de app (decide chamando `POST /_hub/approvals/{id}`; falha volta a `error` e da para repetir; resultado divergente do app = 502), pedidos de acesso (limites: 1 pendente por app, 5 por usuario, 24 h apos recusa; aprovar concede o app, `apps` do usuario = papel + concessoes) e acoes de agente (so sessao; aprovar exige a pagina `/account/agent-approval/{id}`: senha de novo, limite 5/15 min, token de step-up de uso unico 120 s ligado a usuario+pedido+hash). Sino e widget do Inicio vem do mesmo `entries`. SSE: `ready` + `inbox {"unread": n}` so quando o contador muda, keepalive 15 s, 12 conexoes/usuario.
- Configuracoes: Perfil, Aparencia (claro/escuro/automatico), Idioma, Seguranca (troca de senha com `next`: cria sessao nova e volta a pagina), Tokens pessoais (sobre `/api/me/tokens`), Apps autorizados (markup igual; lista vazia ate o OAuth da F4 via `app.state.oauth_grants/oauth_revoke`).
- i18n: idioma = preferencia -> Accept-Language -> pt-BR (`request.state.lang`); shell, menu, paleta, `data-hub-i18n`, home/aplicacoes/status/inbox/busca/conta traduzidos (chaves de `texts.json` e `tr` do catalogo). Tema da preferencia no servidor (`<html data-theme>` + `color-scheme`), sem piscar.

## Paridade (`compare.mjs`, Rust :8080 x Py :8090, claro, pt-BR)
| rota | status | DOM js/cru | pixels 1440/390 |
|---|---|---|---|
| `/search?q=contrato` | 200/200 | 0/0 e 0/0 | 0.00 / 0.00 |
| `/inbox` | 200/200 | 0/0 e 0/0 | 0.00 / 0.00 |
| `/account/settings` | 200/200 | 0/0 e 0/0 | 0.00 / 0.00 |
| `/account/tokens` | 200/200 | 0/0 e 0/0 | 0.00 / 0.00 |
| `/account/authorized-apps` | 200/200 | 0/0 e 0/0 | 0.00 / 0.00 |
| `/`, `/login`, `/applications`, `/status` | 200/200 | 0/0 | 0.00 / 0.00 |

`PARITY_LANG=en`: `/`, `/inbox`, `/account/settings`, `/account/tokens`, `/applications`, `/status` PASS; `/search?q=contrato` so difere no trecho `<mark>` (o Rust traduz "Contrato" -> "Contract" dentro do destaque; nao replicado, e dado do usuario).
Exclusoes novas em `ignore.json` (justificadas em `_why`): lista da Inbox, grupo interno e aviso "parcial" da busca, linhas de tokens pessoais (dados de cada hub).

## Qualidade
`check.sh` com Postgres descartavel: ruff, format, ty, audit ok; 88 passed; cobertura 88,2% (hub.ux 92-100%). `tests/test_inbox.py` cobre regras, API, decisao via app simulado, step-up, SSE (gerador), busca federada/timeout/parcial/ver todos, paginas de conta, troca de senha, idioma.
`.dev.env` ganhou `HUB_INTERNAL_TOKEN_<APP>` (tokens dos apps demo; arquivo ignorado pelo git).

## Lacunas
- Nada alimenta a Inbox ainda: outbox dos apps -> `InboxStore.push` e F3/F5; pedidos de agente (MCP) -> `push` com `kind="agent"` e F4 (a pagina de aprovacao ja funciona). Alertas da plataforma (`a:`) na Inbox: F5.
- Token interno via env; cofre/rotacao e F3. Sem SSO (reautenticacao so por senha).
- Analytics de uso (`Ev::*`, `/api/me/analytics` segue 204) e F5. Login/senha/OAuth ainda so pt-BR.
- Layout do status publico e telas de Admin nao traduzidos (F3/F5).
