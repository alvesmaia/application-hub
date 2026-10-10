# F5a - Tarefas duraveis, outbox, lideranca, agendador de backups

Codigo em `platform-py/hub/ops/`: `leader.py` (lease `hub-leader`, sinal de vida, varredura), `background.py` (liga tudo no `lifespan`), `jobs.py` (jobs em `hub.jobs`), `outbox.py`, `scheduler.py` + cron em `schedule.py`, `backups.py` (copia externa, banco de lado, lista), alertas e snapshot em `routes.py`. `tzdata` entrou nas dependencias.

- **Jobs**: ids da sequencia do banco, gravacao a cada mudanca, `GET /api/ops/jobs/{id}` responde de qualquer replica, um backup/restauracao por app tambem entre replicas, jobs de replica morta viram "Interrompido". Card "Fila de tarefas" mostra os jobs do hub (todas as replicas). Tarefas dos apps (retry/cancel) ja eram via `/_hub/jobs` (F3c).
- **Outbox -> Inbox**: so o lider coleta (5 s, `HUB_OUTBOX_POLL_S`); auditoria durável com `source` -> registro -> ack (so lider com fence); `notification`/`approval_request` -> Inbox + SSE; reentrega deduplicada; event_id invalido nao confirmado. Alertas da plataforma (falha de backup/restauracao/copia externa, queima de SLO) vao para a Inbox dos administradores.
- **Lideranca**: `GET /api/ops/replicas`; agendador, outbox, varreduras (registros vencidos, confirmacoes/pedidos MCP vencidos, jobs orfaos, restauracao de replica morta) e SLO so no lider. Inbox recarrega `inbox.json` quando outra replica grava.
- **Backups**: agendador (cron/rotulos, fuso `HUB_BACKUP_TZ`, jitter, execucao perdida executar/pular, adiar se ocupado, `HUB_BACKUP_TICK_S`), tela mostra proxima/ultima; copia externa `HUB_BACKUP_OFFSITE_URL` com leitura de volta; snapshot "Pre-restauracao" (`HUB_RESTORE_SAFETY_SNAPSHOT=0` desliga); restauracao nao transacional com banco de lado (volta ao nome se falhar), lista/devolver/apagar bancos de lado; manutencao automatica na restauracao.

## Verificado
- `check.sh` verde com Postgres descartavel: 326 passed, cobertura 91%. Testes novos `tests/test_f5a.py` (14).
- Ao vivo `:8090` (so `hubpy-web` reconstruido): segunda replica temporaria `hubpy-web-2` (removida): so uma lider; politica "A cada 1 min" no ativos rodou so no lider (jobs 1,2); parado o lider, a replica 2 assumiu (fence 7) e rodou o job 3. Politica original restaurada.

## Lacunas
- Os backups ao vivo falham ("Falha no runtime"): o `hubpy-web` nao tem o socket Docker (anterior a F5a); logica coberta por testes com Docker simulado.
- Clone para staging, blue/green, supervisao de containers: F5b.
- Estado em arquivos (`inbox.json`, `ops.json`) em volume compartilhado: sem trava entre replicas na escrita (recarga por mtime); `OutboxIngest` por arquivo.
- Telemetria/probes continuam por replica (so alertas de SLO sao do lider). Sem varredura de tokens pessoais vencidos (so confirmacoes/pedidos).
- ui-sweep nao rodado.
