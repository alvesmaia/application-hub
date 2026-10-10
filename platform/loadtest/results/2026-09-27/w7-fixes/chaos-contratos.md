# Teste de caos — contratos (Python/FastAPI) — 2026-09-27

Roteiro: `node loadtest/chaos/chaos-contratos.mjs --rows 100000 --burst 200 --kills 2 --hub-kills 1 --db-restarts 1`
(compose no ar; execução MUKEC95E; imagem hub-contratos). Resultado: **PASSOU** em 105 s.

## Cenário

- Importação de `chaos.xlsx`: 100.000 linhas (5.4 MB), 99.000 válidas e 1.000 com erro (`python -m contratos count-xlsx`, python-calamine).
- Rajada de 200 cadastros de contrato (5 clientes) + onda de 60 cadastros concorrentes 150 ms antes de cada derrubada do app, cada um com `Idempotency-Key` própria e até 20 tentativas com a MESMA chave em erro de rede/502/503/504.
- `docker kill hub-app-contratos` 2× (≥ 20% e ≥ 60%), `docker kill hub-web` 1× (≥ 40%), `docker kill hub-db` 1× (≥ 80%). Lease 15 s.

## Linha do tempo

- 0.3 s — gerando planilha com 100000 linhas (prefixo CXMUKEC95E-) — openpyxl write-only, na imagem hub-contratos
- 16.2 s — planilha: 5.4 MB · 99000 válidas · 1000 rejeitadas (python-calamine, sem banco)
- 16.4 s — importação job-a0e6a869f06e12581a3b4b4ddd13458e enfileirada (HTTP 202)
- 16.4 s — rajada: 200 cadastros, 5 clientes, Idempotency-Key por cadastro, até 20 tentativas
- 21.7 s — docker kill hub-app-contratos #1 com 21000/100000 linhas (21%), 60 cadastros em voo
- 25.2 s — hub-app-contratos não voltou sozinho: docker start
- 40.1 s — docker kill hub-web #1 com 42000/100000 linhas (42%) — coleta do outbox interrompida
- 45.3 s — hub-web de volta
- 46.5 s — docker kill hub-app-contratos #2 com 97000/100000 linhas (97%), 60 cadastros em voo
- 50.0 s — hub-app-contratos não voltou sozinho: docker start
- 52.4 s — docker kill hub-db #1 com 98000/100000 linhas (98%) — Postgres derrubado no meio da importação
- 71.1 s — hub-db de volta (recuperação do WAL)
- 74.1 s — importação terminou: IMPORTAÇÃO CONCLUÍDA (100000/100000)
- 103.8 s — rajada terminou: 320 cadastros

## Verificação

| Verificação | Resultado | Detalhe |
|---|---|---|
| Importação concluída | ✅ | estado done |
| Retomada a partir do checkpoint (tentativas > derrubadas do app) | ✅ | 3 tentativas, 2 derrubadas |
| Contratos gravados == linhas válidas da planilha | ✅ | gravados 99000 (checkpoint) · 99000 no banco · esperado 99000 |
| Sem duplicados (número único) | ✅ | 99000 linhas, 99000 números distintos |
| Sem lote parcial (checkpoint == banco, erros sem duplicar) | ✅ | gravados + rejeitados = 100000 de 100000; 1000 linhas no relatório de erros |
| Toda gravação da rajada aplicada exatamente uma vez | ✅ | 320 contratos para 320 cadastros |
| Nenhuma nova tentativa virou duplicado/erro | ✅ | {"criado":316,"já concluída":4} |
| Um evento "Contrato cadastrado" por cadastro | ✅ | 320 eventos, 320 alvos |
| Eventos da importação: iniciada + concluída, uma vez cada | ✅ | Importação de contratos iniciada, Importação de contratos concluída |
| Outbox totalmente confirmado | ✅ | 0 pendentes |
| Todo evento do app chegou ao hub | ✅ | 322 eventos do app · 0 fora da auditoria do hub |
| Nenhum evento duplicado na auditoria | ✅ | 0 duplicados |
| Registro de ingestão do hub sem pendências | ✅ | 5717 gravados · 0 aguardando confirmação |
| Derrubadas executadas (app, hub, banco) | ✅ | app 2/2 · hub-web 1/1 · hub-db 1/1 |
| Memória abaixo do limite do container | ✅ | pico 162 MiB de 1024 MiB (390 amostras) |

Rajada: 320 cadastros (320 esperados) · 111 com nova tentativa (máx. 8) · 10 respostas repetidas pelo registro de idempotência · desfechos {"criado":316,"já concluída":4}.
Tarefa `job-a0e6a869f06e12581a3b4b4ddd13458e`: 3 tentativas; checkpoint final: 99000 gravados, 1000 rejeitados, última linha 100001.
Memória do container (docker stats): pico 162 MiB de 1024 MiB — por fase: importação 162 MiB · depois 128 MiB.
