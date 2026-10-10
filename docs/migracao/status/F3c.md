# F3c - Operacoes: Implantacoes, Containers, Tarefas, Backups (+ diffs herdados da F3b)

Codigo em `platform-py/hub/ops/`: `routes.py` (paginas `/admin/{deploys,runtime,tasks,backups}` e API `/api/ops/*`), `model.py` (`ops.json`, transicoes de implantacao, aprovacao de scripts), `pipeline.py` (7 passos + zip defensivo), `runtime.py` (Docker SDK, `hubpy-app-*`, host/limites/admission), `jobs.py` (jobs em memoria + tarefas dos apps), `backups.py` (credenciais minimas, pacote, restauracao), `schedule.py`. Templates `ops_{deploys,runtime,tasks,backups}.html`; JS `ops.js` e CSS originais. RBAC ja coberto pelo middleware (`editar_configuracoes`; `rotate` = `gerenciar_variaveis`). Dependencias novas: `docker`, `python-multipart`.

- **Implantacoes**: upload multipart (`X-Hub-Request`, `wait=true`, 202 + job), pipeline Manifest/Assinatura(checksums)/Seguranca/Contrato(digest, SDK, tarefas, OpenAPI)/Front-end(sem JS inline, `manifest.scripts` -> sha384 SRI, REVISE)/Smoke/Aprovacao; promover; revisao de scripts com separacao de funcoes (`HUB_SCRIPTS_ALLOW_SELF_APPROVAL`), tudo auditado.
- **Containers**: lista/estado/CPU/memoria reais via Docker SDK, logs (+SSE por polling), inspect sem valores de env, start/stop/restart (so containers de apps registrados), limites com validacao + admission control + `update` no container vivo; engine inacessivel mostra o aviso.
- **Tarefas**: lista de `/{id}/_hub/jobs` com token interno, filtros `?app&status`, sagas, reprocessar/cancelar auditados.
- **Backups**: politicas (cron/rotulos), "Backup agora" -> zip em `HUB_DATA_DIR/backups/<app>/` (pg_dump via `docker exec` no `hubpy-app-db`, `mc mirror` do bucket, volumes, sha256 por parte), retencao, restauracao transacional (`psql -1`), teste de restore em banco temporario, rotacao de credenciais. Credenciais minimas portadas de `creds.rs`: papel `hub_bkp_<db>` (so leitura, marca `hub-managed`, papel alheio recusado) e usuario `bkp-<bucket>` (politica GetObject); segredos no cofre (`ops.json` guarda so `vault:v1:`).

## Paridade (`compare.mjs`, Rust :8080 x Py :8091, claro, pt-BR)
| rota | DOM js/cru | pixels 1440/390 |
|---|---|---|
| `/admin/deploys` | 0/0 | 0.00 / 0.00 |
| `/admin/runtime` (+`?runtime=podman`) | 0/0 | 0.00 / 0.00 |
| `/admin/tasks` | 0/0 | 0.00 / 0.00 |
| `/admin/backups` (+`--click [data-ops-backup-creds]`) | 0/0 | 0.00 / 0.00 |
| `/applications`, `/admin/env`, `/admin/connections` | 0/0 | 0.00 / 0.00 |

Novas exclusoes em `ignore.json` (cada uma com `_why`): tabelas de dados (implantacoes, containers + drawer, tarefas, politicas, historico), barras/legenda de capacidade, metricas dos cartoes de backup, texto `data-ops-rollout` (Python: ativacao e F5), banners de apps sem resposta.

## Diffs herdados da F3b (Py em :8091)
- `/admin/connections` e `/admin/env`: causa = hub nativo iniciado **sem** `HUB_APP_ENV_FILE`, entao nao tinha as variaveis `DATABASE_URL_*`/conexoes iniciais. Com `HUB_APP_ENV_FILE=platform/apps/hub-web/app-env.compose.json` (como o compose) `/admin/connections` passa sem ignore. Em `/admin/env` sobra so conjunto/ordem das variaveis a partir da 7a (E2E e `HUB_EXTAPI_URL` so no Rust): ignorado (`tr:nth-child(n+7)`).
- `/applications`: pilha (Rust x Python), bibliotecas e versao vem do registro de cada hub (apps Rust v2.4 x Python v2.5): diferenca esperada da migracao (F6); ignorado `.hub-stack dd, .app-version`.

## Fluxos reais (`node platform-py/parity/flows-f3c.mjs`, Docker real, so `hubpy-*`)
Importar pacote (pipeline pintado, rejeicao por segredo, scripts a revisar, separacao de funcoes, aprovar -> 503 F5), drawer logs/inspect, limites, restart do `hubpy-app-ativos`, filtros de tarefas, politica, backup real (papel `hub_bkp_app_coletor` sem superusuario), teste de restore em banco temporario removido, rotacao, auditoria; sem erros de console. Tudo ok.

## Qualidade
`check.sh` (agora com `--cov=hub.ops`): 212 passed, 7 skipped; cobertura total 89,5%. `tests/test_ops.py`: RBAC, auditoria das escritas, Docker simulado (`FakeDocker`), pipeline (zip-slip, segredos, JS inline, OpenAPI), scripts, tarefas, backup/restore/retencao/rotacao, vault; 1 teste `@pytest.mark.docker` opcional (`DOCKER_AVAILABLE=1`).

## Lacunas / F5
- **Blue/green**: aprovar, rollback e "Aplicar (reiniciar)" validam a transicao e respondem **503** com mensagem F5; upload em staging so valida/registra (fica "Aguardando aprovacao"); promover so cria o registro de producao. Sem `__prev`, health check, troca atomica, supervisao/autorreinicio, staging por app (todas as linhas sao producao).
- **Agendador de backups** (proxima execucao, execucao perdida, jitter) e cron real: so validacao; a tela diz "execucao automatica na F5". Sem copia externa (offsite), bancos de lado (restauracao e transacional, lista sempre vazia), clone para staging (503), snapshot "Pre-restauracao", estado de manutencao automatico durante restauracao, trava por app entre backup/deploy (so 1 job de backup/restore por app), lideranca multi-replica (`ops.json` por processo).
- Bucket: backup/restauracao via `mc` (precisa de `HUB_S3_ADMIN_URL`/`mc` no hub); sem object store o backup so do banco/volumes segue.
- Escopo de token por app/ambiente nas rotas `/api/ops` (so a acao, via middleware); `provision`, `rotate` e `manifest/validate` de `/api/ops` nao portados aqui (rotate ja existia na F3a).
- Compose `hubpy-web` nao monta o socket do Docker: no container a tela mostra "Runtime indisponivel" ate o dono liberar `/var/run/docker.sock` (compose base protegido, nao alterado).
- ui-sweep nao rodado nesta etapa (so paridade e fluxos).
