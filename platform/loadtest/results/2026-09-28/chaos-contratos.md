# Teste de caos — contratos (Python/FastAPI) — 2026-09-28

Roteiro: `node loadtest/chaos/chaos-contratos.mjs --rows 250000 --burst 300 --kills 2 --hub-kills 1 --db-restarts 1`
(compose no ar; execução MUL8CMBY; imagem hub-contratos). Resultado: **PASSOU** em 140 s.

## Cenário

- Importação de `chaos.xlsx`: 250.000 linhas (13.5 MB), 247.500 válidas e 2.500 com erro (`python -m contratos count-xlsx`, python-calamine).
- Rajada de 300 cadastros de contrato (5 clientes) + onda de 60 cadastros concorrentes 150 ms antes de cada derrubada do app, cada um com `Idempotency-Key` própria e até 20 tentativas com a MESMA chave em erro de rede/502/503/504.
- `docker kill hub-app-contratos` 2× (≥ 20% e ≥ 60%), `docker kill hub-web` 1× (≥ 40%), `docker kill hub-db` 1× (≥ 80%). Lease 15 s.

## Linha do tempo

- 0.4 s — gerando planilha com 250000 linhas (prefixo CXMUL8CMBY-) — openpyxl write-only, na imagem hub-contratos
- 33.9 s — planilha: 13.5 MB · 247500 válidas · 2500 rejeitadas (python-calamine, sem banco)
- 34.2 s — importação job-40e760475f7a31f43991dfeea37041f2 enfileirada (HTTP 202)
- 34.2 s — rajada: 300 cadastros, 5 clientes, Idempotency-Key por cadastro, até 20 tentativas
- 41.6 s — docker kill hub-app-contratos #1 com 58000/250000 linhas (23%), 60 cadastros em voo
- 45.0 s — hub-app-contratos não voltou sozinho: docker start
- 62.6 s — docker kill hub-web #1 com 100000/250000 linhas (40%) — coleta do outbox interrompida
- 67.7 s — hub-web de volta
- 68.8 s — docker kill hub-app-contratos #2 com 167000/250000 linhas (67%), 60 cadastros em voo
- 72.4 s — hub-app-contratos não voltou sozinho: docker start
- 90.0 s — docker kill hub-db #1 com 209000/250000 linhas (84%) — Postgres derrubado no meio da importação
- 97.1 s — hub-db de volta (recuperação do WAL)
- 112.3 s — importação terminou: IMPORTAÇÃO CONCLUÍDA (250000/250000)
- 139.3 s — rajada terminou: 420 cadastros

## Verificação

| Verificação | Resultado | Detalhe |
|---|---|---|
| Importação concluída | ✅ | estado done |
| Retomada a partir do checkpoint (tentativas > derrubadas do app) | ✅ | 4 tentativas, 2 derrubadas |
| Contratos gravados == linhas válidas da planilha | ✅ | gravados 247500 (checkpoint) · 247500 no banco · esperado 247500 |
| Sem duplicados (número único) | ✅ | 247500 linhas, 247500 números distintos |
| Sem lote parcial (checkpoint == banco, erros sem duplicar) | ✅ | gravados + rejeitados = 250000 de 250000; 2500 linhas no relatório de erros |
| Toda gravação da rajada aplicada exatamente uma vez | ✅ | 420 contratos para 420 cadastros |
| Nenhuma nova tentativa virou duplicado/erro | ✅ | {"criado":412,"já concluída":8} |
| Um evento "Contrato cadastrado" por cadastro | ✅ | 420 eventos, 420 alvos |
| Eventos da importação: iniciada + concluída, uma vez cada | ✅ | Importação de contratos iniciada, Importação de contratos concluída |
| Outbox totalmente confirmado | ✅ | 0 pendentes |
| Todo evento de auditoria do app chegou ao hub | ✅ | 422 eventos de auditoria · 0 fora da auditoria do hub |
| Todo aviso do app chegou à Inbox exatamente uma vez | ✅ | 1 avisos (notification) · 0 fora da Inbox · 0 duplicados |
| Nenhum tipo desconhecido no outbox | ✅ | 0 () |
| Nenhum evento duplicado na auditoria | ✅ | 0 duplicados |
| Registro de ingestão do hub sem pendências | ✅ | 422 gravados · 0 aguardando confirmação |
| Derrubadas executadas (app, hub, banco) | ✅ | app 2/2 · hub-web 1/1 · hub-db 1/1 |
| Memória abaixo do limite do container | ✅ | pico 281 MiB de 1024 MiB (526 amostras) |

Rajada: 420 cadastros (420 esperados) · 95 com nova tentativa (máx. 5) · 13 respostas repetidas pelo registro de idempotência · desfechos {"criado":412,"já concluída":8}.
Tarefa `job-40e760475f7a31f43991dfeea37041f2`: 4 tentativas; checkpoint final: 247500 gravados, 2500 rejeitados, última linha 250001.
Memória do container (docker stats): pico 281 MiB de 1024 MiB — por fase: importação 281 MiB · depois 163 MiB.
