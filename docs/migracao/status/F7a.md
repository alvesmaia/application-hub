# F7a - validacao pesada do hub Python (:8090) antes do cutover

**Veredito: GO** (leftovers justificados abaixo; nenhum bloqueia o cutover).

## Suites (antes -> depois)
| Suite | Antes | Depois |
|---|---|---|
| ui-sweep completo (3 papeis x 4 temas, 56 rotas) | 63 achados fora da allowlist | restam apenas residuos de dados de teste (ver abaixo) |
| Playwright completo | 92 pass / 21 fail / 33 skipped | rodada completa final: 127 passed, 3 failed (07 justificado; 10-acesso e 13-oauth passam isolados), 12 skipped em cascata do 10 -> reexecutados 10+11+13 em separado: 14 passed |
| Conformidade dos 5 apps (`platform-py/scripts/conformance.sh`) | nao rodada | 5/5: ativos 28, fornecedores 25, servicos 26, contratos 30, coletor 25 passed; SIGTERM saida 0 em ~1 s |
| mcp-client independente (SDK oficial) | nao rodado | todas as verificacoes passaram (OAuth+PKCE, refresh, aprovacao destrutiva, token pessoal) |
| `uv audit` (sdk-python, cli, platform-py) | - | sem vulnerabilidades (`platform/conformance` nao tem tabela `project`) |
| DR (`loadtest/py-dr.mjs`) | nao rodado | OK: DROP DATABASE + bucket vazio -> restauracao pela API; 7 tabelas (md5) e 15 anexos (sha256) identicos; RTO 10,3 s (contratos) / 1,1 s (fornecedores) |
| Zero downtime (`py-resilience.mjs zd`, 60 req/s) | nao rodado | 2252 req, 2252 2xx durante Aplicar x2 e implantacao+aprovacao blue/green. Rollback falhou com seguranca (imagem do baseline do compose foi substituida por rebuild; producao e trafego intocados) |
| Caos (`py-resilience.mjs chaos`) | nao rodado | docker kill do app x2 (volta em 12-30 s, 502 so no app morto), restart do hub (8 s) e do banco de estado (1,4 s, 8x503); banco do contratos 280063 linhas antes e depois |
| Staging equivalente a 07-staging | - | `/hub-env?to=staging` grava cookie HttpOnly SameSite=Strict, gateway 404 sem container de staging, saida limpa o cookie |

Os scripts originais de DR/zero downtime/caos/conformidade sao so da pilha Rust (nomes `hub-*`, `hub-web`): nao foram executados. Equivalentes novos so tocam `hubpy-*`: `platform/loadtest/py-dr.mjs`, `py-resilience.mjs`, `platform-py/scripts/conformance.sh`.

## Bugs do port corrigidos (todos com teste pytest)
- `/admin/sdk` 404 (+ `POST /api/ops/manifest/validate`); `/offline`; manifesto PWA `?start=` + atalhos; link do manifesto por app.
- i18n: `/assets/i18n/{en,es}.json` e traducao de erros JSON (`hub/ux/texts.py`, middleware).
- CSP de pagina de app ignorava scripts aprovados (stub vazio) -> provider ligado ao ops.
- Tour guiado nunca abria (`data-hub-tour` fixo); modo do rail por cookie `hub_rail_<ctx>`; texto do carregador.
- 403 de pagina agora dentro do shell (eyebrow + titulo).
- Anti-CSRF: `Origin: null` com `Sec-Fetch-Site: same-origin` (consentimento OAuth) e Bearer + cookie vazado.
- Assets: ETag/304 e cache como o Rust + cache em memoria (bind mount lento fazia o JS carregar tarde).
- Auditoria: checkpoints assinados a cada 100 eventos; `GET /api/admin/vault`; `active` em `/api/me/tokens`.
- Rollback voltava ao baseline do compose (id maior) em vez da implantacao anterior.
- 404 em implantacao inexistente; Inbox: link de pedido de agente, aviso "Acesso a X liberado/recusado", revoke aceita `i:N`.
- Gateway nao repassava o idioma (`x-hub-locale`); `/assets/theme.css` (white label); COOP `noopener-allow-popups` nas telas de credencial; politica MinIO `hub-<user>`; auditoria de storage com escopo do app; POST sem corpo nas Conexoes dispensa Content-Type; mensagem do Aplicar.
- Infra (compose): cada app tinha o MESMO banco `hub` -> outbox misturado (aprovacoes do servicos nunca chegavam a Inbox). Agora um banco por app (`infra/app-db-init.sh`, `app-env.json`); apps com 1 GiB (256m dava OOM na importacao de 200 mil linhas).
- Rotas de teste anti-phishing (`hub_sdk.e2e_hostile`, so com `HUB_DEMO_E2E_HOSTILE=1`) nos apps ativos/servicos.

## Testes ajustados (justificativa)
- Nomes `hub-*` parametrizados por `E2E_CONTAINER_PREFIX`/`E2E_DATA_NETWORK`/`E2E_OBJSTORE_PASS`/`E2E_IMAGE_<APP>`/`E2E_ATIVOS_STACK`/`HUB_BIN` (padroes = pilha Rust).
- `06-ops`: confirmacao do modal do hub (`confirmHub`), pois o `autoConfirm` so age em acoes com locator.
- `14`: texto "Revisar: o app traz JavaScript proprio" (a UI Rust ja era assim; teste desatualizado).
- `global-teardown` limpa `__staging` pelo prefixo configurado.

## Leftovers justificados
- `07-staging`: assume staging por host separado (`staging.localhost`, sessao `hub_stg`); o Python usa cookie (documentado em F5b). Coberto pela checagem manual acima.
- ui-sweep: restos de dados de teste (script `e2e-tampered.js` aprovado no servicos, itens de aprovacao antigos que respondem 404) e tabelas largas em `/admin/deploys` e `/admin/health` a 1280 px (cosmetico).
- `10-onda9` (pedido de acesso) falha na rodada completa (espera 403 e ve 200: estado deixado por teste anterior da mesma rodada) e `13-oauth` oscila; ambos passam isolados (14 passed junto com 10/11) e derrubam em cascata os 12 testes seguintes do 10/11, que tambem passaram em separado.
- Alguns testes sao sensiveis a ordem/estado (aprovacao de acesso deixa concessao se falhar no meio; flakes isolados em `02-apps`, `13-oauth` passam ao reexecutar).
- Rollback ao baseline do compose depende da imagem original ainda existir no daemon.
- Backups do ativos/servicos (banco compartilhado grande) nao exercitados no DR; DR cobriu contratos (banco provisionado) e fornecedores (bucket).
