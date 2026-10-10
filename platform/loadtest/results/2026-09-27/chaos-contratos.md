# Teste de caos — contratos (Python/FastAPI) — 2026-09-27

Roteiro: `node loadtest/chaos/chaos-contratos.mjs --rows 250000 --burst 300 --kills 2 --hub-kills 1 --db-restarts 1`
(compose no ar; execução MUKC24IS; imagem hub-contratos). Resultado: **PASSOU** em 140 s.

## Cenário

- Importação de `chaos.xlsx`: 250.000 linhas (13.5 MB), 247.500 válidas e 2.500 com erro (`python -m contratos count-xlsx`, python-calamine).
- Rajada de 300 cadastros de contrato (5 clientes) + onda de 60 cadastros concorrentes 150 ms antes de cada derrubada do app, cada um com `Idempotency-Key` própria e até 20 tentativas com a MESMA chave em erro de rede/502/503/504.
- `docker kill hub-app-contratos` 2× (≥ 20% e ≥ 60%), `docker kill hub-web` 1× (≥ 40%), `docker kill hub-db` 1× (≥ 80%). Lease 15 s.

## Linha do tempo

- 0.3 s — gerando planilha com 250000 linhas (prefixo CXMUKC24IS-) — openpyxl write-only, na imagem hub-contratos
- 34.7 s — planilha: 13.5 MB · 247500 válidas · 2500 rejeitadas (python-calamine, sem banco)
- 35.0 s — importação job-2c21f68ac0ad3b4507b51e0460dd3cd2 enfileirada (HTTP 202)
- 35.0 s — rajada: 300 cadastros, 5 clientes, Idempotency-Key por cadastro, até 20 tentativas
- 45.5 s — docker kill hub-app-contratos #1 com 54000/250000 linhas (22%), 60 cadastros em voo
- 49.0 s — hub-app-contratos não voltou sozinho: docker start
- 68.6 s — docker kill hub-web #1 com 110000/250000 linhas (44%) — coleta do outbox interrompida
- 73.9 s — hub-web de volta
- 75.0 s — docker kill hub-app-contratos #2 com 170000/250000 linhas (68%), 60 cadastros em voo
- 78.6 s — hub-app-contratos não voltou sozinho: docker start
- 96.2 s — docker kill hub-db #1 com 207000/250000 linhas (83%) — Postgres derrubado no meio da importação
- 104.0 s — hub-db de volta (recuperação do WAL)
- 119.2 s — importação terminou: IMPORTAÇÃO CONCLUÍDA (250000/250000)
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
| Nenhuma nova tentativa virou duplicado/erro | ✅ | {"criado":418,"já concluída":2} |
| Um evento "Contrato cadastrado" por cadastro | ✅ | 420 eventos, 420 alvos |
| Eventos da importação: iniciada + concluída, uma vez cada | ✅ | Importação de contratos iniciada, Importação de contratos concluída |
| Outbox totalmente confirmado | ✅ | 0 pendentes |
| Todo evento do app chegou ao hub | ✅ | 422 eventos do app · 0 fora da auditoria do hub |
| Nenhum evento duplicado na auditoria | ✅ | 0 duplicados |
| Registro de ingestão do hub sem pendências | ✅ | 5222 gravados · 0 aguardando confirmação |
| Derrubadas executadas (app, hub, banco) | ✅ | app 2/2 · hub-web 1/1 · hub-db 1/1 |
| Memória abaixo do limite do container | ✅ | pico 335 MiB de 1024 MiB (526 amostras) |

Rajada: 420 cadastros (420 esperados) · 112 com nova tentativa (máx. 5) · 10 respostas repetidas pelo registro de idempotência · desfechos {"criado":418,"já concluída":2}.
Tarefa `job-2c21f68ac0ad3b4507b51e0460dd3cd2`: 4 tentativas; checkpoint final: 247500 gravados, 2500 rejeitados, última linha 250001.
Memória do container (docker stats): pico 335 MiB de 1024 MiB — por fase: importação 335 MiB · depois 254 MiB.
