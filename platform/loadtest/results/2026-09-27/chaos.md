# Teste de caos — 2026-09-27

Roteiro: `node loadtest/chaos/chaos.mjs --rows 250000 --burst 300 --kills 2 --hub-kills 1 --db-restarts 1` (compose da
plataforma no ar; execução MUJEP8B2). Resultado: **PASSOU** em 138 s.

## Cenário

- Importação de `chaos.xlsx`: 250.000 linhas (38.7 MB), 247.548 válidas e 2.452 com erro (validação da planilha sem banco, `hub-demo-apps count-xlsx`).
- Rajada de 300 cadastros de ativo (5 clientes, 1 por ~1,2 s cada) + uma onda de 60 cadastros concorrentes disparada 150 ms antes de cada derrubada (requisições em voo), cada um com `Idempotency-Key` própria e até 20 tentativas do cliente com a MESMA chave em erro de rede/502/503/504 (como o `hub.js`).
- `docker kill hub-app-ativos` 2× durante a importação (≥ 20% e ≥ 60% lidas); o container é religado (`docker start` quando a restart policy não se aplica a `docker kill`). Lease das tarefas: 15 s (`HUB_JOB_LEASE_S`).
- `docker kill hub-web` 1× (≥ 40% lidas; religado com `docker start`): a coleta do outbox é interrompida no meio; ao voltar, reentregas são deduplicadas (registro de ingestão + `source` da trilha) e nada é confirmado antes de a auditoria estar gravada em disco.
- `docker kill hub-db` 1× (≥ 80% lidas; religado com `docker start`): o Postgres cai no meio de um lote; a tentativa falha (transitória) e a tarefa retoma do checkpoint; cadastros em voo recebem 503 e o cliente repete com a mesma chave.

## Linha do tempo

- 0.2 s — gerando planilha com 250000 linhas (prefixo CXMUJEP8B2-)
- 37.8 s — planilha: 38.7 MB · 247548 válidas · 2452 rejeitadas (validação sem banco)
- 38.4 s — importação job-9e7692c6662ee21eef3a26c3b93954a2 enfileirada (HTTP 202)
- 38.4 s — rajada: 300 cadastros, 5 clientes, Idempotency-Key por cadastro, até 20 tentativas
- 43.6 s — docker kill hub-app-ativos #1 com 60000/250000 linhas lidas (24%), 60 cadastros em voo
- 47.1 s — hub-app-ativos não voltou sozinho (restart policy não se aplica a docker kill): docker start
- 62.6 s — docker kill hub-web #1 com 102000/250000 linhas lidas (41%) — coleta do outbox interrompida
- 67.1 s — hub-web de volta
- 68.3 s — docker kill hub-app-ativos #2 com 202000/250000 linhas lidas (81%), 60 cadastros em voo
- 71.8 s — hub-app-ativos não voltou sozinho (restart policy não se aplica a docker kill): docker start
- 73.1 s — docker kill hub-db #1 com 204000/250000 linhas lidas (82%) — Postgres derrubado no meio da importação
- 81.5 s — hub-db de volta (recuperação do WAL)
- 90.6 s — importação terminou: IMPORTAÇÃO CONCLUÍDA (250000/250000)
- 136.3 s — rajada terminou: 420 cadastros

## Verificação

| Verificação | Resultado | Detalhe |
|---|---|---|
| Importação concluída | ✅ | estado done |
| Retomada a partir do checkpoint (tentativas > número de derrubadas) | ✅ | 3 tentativas, 2 derrubadas |
| Linhas gravadas == linhas válidas da planilha | ✅ | gravadas 247548 (checkpoint) · 247548 no banco · esperado 247548 |
| Sem duplicados (patrimônio único) | ✅ | 247548 linhas, 247548 patrimônios distintos |
| Sem lote parcial (contadores do checkpoint == banco) | ✅ | gravadas + rejeitadas = 250000 de 250000 lidas |
| Toda gravação da rajada aplicada exatamente uma vez | ✅ | 420 ativos para 420 cadastros |
| Nenhuma nova tentativa virou duplicado/erro | ✅ | {"criado":411,"já concluída":9} |
| Um evento "Ativo cadastrado" por cadastro | ✅ | 420 eventos, 420 alvos |
| Eventos da importação: iniciada + concluída, uma vez cada | ✅ | Importação de ativos iniciada, Importação de ativos concluída |
| Outbox totalmente confirmado | ✅ | 0 pendentes |
| Todo evento do app chegou ao hub | ✅ | 422 eventos do app · 0 fora da auditoria do hub |
| Nenhum evento duplicado na auditoria | ✅ | 0 duplicados |
| Registro de ingestão do hub compactado (nada aguardando confirmação) | ✅ | 423 gravados desde o início do hub-data · 0 aguardando confirmação |
| Derrubadas executadas (app, hub, banco) | ✅ | app 2/2 · hub-web 1/1 · hub-db 1/1 |

Rajada: 420 cadastros (420 esperados) · 95 precisaram de nova tentativa (máx. 5 tentativas) · 15 respostas repetidas pelo registro de idempotência · desfechos {"criado":411,"já concluída":9}.
Tarefa `job-9e7692c6662ee21eef3a26c3b93954a2`: 3 tentativas (1 + 2 retomadas após lease vencido; 2 derrubadas), checkpoint final: 247548 gravadas, 2452 rejeitadas, 250000 lidas.
Auditoria: 422 eventos gravados no outbox do app durante o teste; todos confirmados e presentes uma vez na trilha do hub (deduplicação por `event_id`).
