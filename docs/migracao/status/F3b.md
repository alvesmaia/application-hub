# F3b - Observabilidade e operacao leve: Logs, Auditoria, Saude/SLOs/Status, Feature flags, Observabilidade, Analytics

Codigo em `platform-py/hub/insight/`: `routes.py` (paginas `/admin/{logs,audit,health,observability,flags,analytics}` e APIs `/api/admin/{logs,audit,health,slos,incidents,flags,analytics}`), `ops.py` (flags, SLOs, incidentes e servicos no `admin.json`, tudo auditado), `flags.py` (`hub-flags`), `metrics.py` (metricas RED, traces, SLOs), `runtime.py` (instrumentacao do gateway e tarefas), `analytics.py`, `fmt.py`. Templates `admin_{logs,audit,health,flags,obs,analytics}.html`; `_status_body.html` agora mostra incidentes. JS (`admin.js`, `logshare.js`, `insights.js`, `ux.js`) e CSS inalterados.

- **Logs**: `LogBus` (`admin/store.py`) com filtros (app, intervalo, usuario por substring, nivel e origem em lista), console da pagina, `GET /api/admin/logs`, tempo real por SSE (`/logs/stream`, evento `ready` + `log`, keep-alive de 15 s) e `/logs/stats`. Linhas reais: toda escrita auditada (`[admin]`) e cada requisicao `/_frag` do gateway (`[gateway] GET /x -> 200 (12 ms)`).
- **Auditoria**: pagina com chips por escopo, `split_ids` (`HUB_*`/`hubt_` em `<code>`), `GET /api/admin/audit` (mais recente primeiro, `scope`, `limit`), `POST /audit/verify` (mesmo JSON do Rust: ok, entries, checkpoints, segments, from_seq, migrated, anchor_seq, problems; o resultado tambem vira evento) e `GET /audit/export` (JSONL da cadeia + linha `proof` com cabeca assinada e verificacao). Cadeia adulterada: verificacao acusa e a pagina mostra o bloqueio.
- **Saude / SLOs / Status**: servicos monitorados (CRUD, "Verificar agora" sonda `GET {upstream}/{id}`), SLOs (disponibilidade e latencia sobre os baldes de 5 min, orcamento restante, queima 1 h/5 min), incidentes (manual: registrar, nota, resolver; automatico: queima rapida >= 14,4x abre/fecha incidente), pagina `/status` (e Inicio/Aplicacoes) com situacao real (incidente aberto > sonda > trafego), barras de 30 dias, linha de SLO e incidentes. RBAC como o Rust: ver = Configuracoes ou Auditoria; alterar = Configuracoes.
- **Feature flags**: CRUD, kill switch por ambiente, regras por funcao/usuario, percentual (hash estavel `chave:usuario`, vetores do Rust em `tests/vectors/flags-vectors.json`), tudo auditado no escopo do app. O gateway avalia e envia `x-hub-flags` ao app; o hub empurra as definicoes a `/{app}/_hub/flags` (usuarios como resumos sha256) a cada mudanca e a cada 30 s.
- **Observabilidade**: metricas em processo (`HUB_TELEMETRY=0` desliga), graficos RED/CPU/memoria em SVG (mesmo markup), cartoes, traces mais lentos e cascata; spans do gateway e das acoes do plano de controle; coleta de spans/medidores dos apps (`/{id}/_hub/telemetry`, token interno, validacao e redacao como o Rust). `metrics.json` guarda os baldes de 5 min (30 dias).
- **Analytics**: agregados por dia com ids opacos (HMAC; chave no cofre, troca a cada retencao e sob demanda), DAU/WAU/MAU, funil, secoes/paginas, buscas sem resultado (so hash, texto so com opt-in), onboarding, opt-out por usuario, retencao 7-400 dias, CSV com neutralizacao de formulas. APIs `/api/admin/analytics`, `.csv`, `retention`, `terms`, `rotate-key`; eventos vindos do login, do gateway, das paginas, da busca e de `/api/me/analytics`.

## Paridade (`compare.mjs`, Rust :8080 x Py :8091, claro, pt-BR; Py = hub local com `apps.compose.json`, `HUB_TELEMETRY=0`)
| rota | DOM js/cru | pixels 1440/390 |
|---|---|---|
| `/admin/logs` | 0/0 e 0/0 | 0.00 / 0.00 |
| `/admin/audit` | 0/0 e 0/0 | 0.00 / 0.00 |
| `/admin/health` | 0/0 e 0/0 | 0.00 / 0.00 |
| `/admin/observability` | 0/0 e 0/0 | 0.00 / 0.00 |
| `/admin/flags` | 0/0 e 0/0 | 0.00 / 0.00 |
| `/admin/analytics` | 0/0 e 0/0 | 0.00 / 0.00 |
| `/status` | 0/0 e 0/0 | 0.00 / 0.00 |
| `/` (widget de status) | 0/0 e 0/0 | 0.00 / 0.00 |

Com modal/menu aberto (`--click`): Novo servico, Definir SLO, menu da linha de saude, Nova flag, Regras de flag, Registrar incidente, Salvar retencao: todos PASS (0 estruturais, 0.00%).
Exclusoes novas em `ignore.json` (cada uma com `_why`): linhas do console de logs e da auditoria, celulas de status/latencia/ultima verificacao e linhas dos apps na Saude, celulas de metricas das linhas de SLO, contagens e tabelas do Analytics, cartoes/graficos/traces da Observabilidade, versao das flags e meta do Analytics (dados de cada hub). Os dados de flags, SLO e servicos do Python foram alinhados aos do Rust pela propria API (flag `novo-resumo`, SLO de Ativos, 3 servicos).
Nao e desta etapa (dados do ambiente): `/applications` (stack Rust x Python dos apps no registro, F6) e `/admin/env`, `/admin/connections` (variaveis `DATABASE_URL_*` e conexoes provisionadas so no hub Rust; F5).
Fluxos reais no navegador (`node platform-py/parity/flows-f3b.mjs`, so hub Python): criar/ligar/regra de percentual/excluir flag, definir/remover SLO, registrar/anotar/resolver incidente (status page), servico monitorado, verificar integridade + exportar JSONL, filtros e tempo real de logs, trace e cascata, retencao e CSV do analytics; sem erros de console. 27/27.

## Qualidade
`check.sh` (ruff, format, ty, audit; `--cov=hub.insight` agora incluido): 184 passed, 6 skipped; cobertura total 89,1% (`hub.insight` 93-99% por modulo). Novos: `test_insight.py` (vetores de flags do Rust, avaliacao, metricas/percentis/SLO/queima/uptime, traces, redacao, analytics com privacidade, filtros de log, RBAC por funcao em paginas e APIs, escritas auditadas e cadeia valida, gateway observado com `x-hub-flags`/`traceparent`/metricas/logs, cascata, ingestao validada, saude/SLO/incidentes/status, incidente automatico, verificar/exportar/adulterar auditoria, analytics e opt-out, tarefas de fundo).

## Lacunas
- Exportacao **OTLP** (opcional) nao portada: `OTEL_EXPORTER_OTLP_ENDPOINT` e ignorado (a pagina informa "OTLP desligado").
- **CPU/memoria dos containers** e o card "Fila de tarefas" dependem do runtime Docker/jobs (F5); os graficos mostram "Sem dados nesta janela". `HUB_RUNTIME`/staging: so producao.
- Linhas `[container]` no console de logs (saida dos containers) e `[auth]`/`[hub]` (staging, busca) nao existem ainda; origens `container`/`ops` ficam vazias ate a F5.
- Auditoria: sem ancora externa (`HUB_AUDIT_HEAD_URL/FILE`; a pagina mostra "so local"), sem segmentos arquivados/retencao assinada (cadeia unica), verificacao nao compara a trilha de `admin.json` com a cadeia, exportacao nao grava checkpoint antes.
- Alertas do **sino** (notificacoes de admin, `/api/admin/notifications`) e "Cofre" (`/api/admin/vault`) nao portados; a queima de SLO so abre incidente na status page.
- Telemetria por replica (sem lideranca): com varias replicas cada uma tem suas metricas (F5).
- Telas ainda so em pt-BR (como a F3a).
