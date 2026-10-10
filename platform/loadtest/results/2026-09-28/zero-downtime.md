# Implantação sem indisponibilidade sob carga — 2026-09-28

Gerado por `loadtest/zero-downtime.mjs` (compose no ar; o override VM de destino é opcional).
Carga: `oha` a 50 RPS (c=64, `--latency-correction`) pelo gateway (90% fragmento
`/_frag/ativos/dashboard`, 10% lista `/_frag/ativos/assets?q=NT-1`), durante 160 s,
+ sonda própria a 20 RPS (75% fragmento, 25% lista; pela porta
publicada) com carimbo de tempo para medir as janelas das trocas, + uma exportação CSV do
`ativos` lida devagar (~20 s) começando antes da 1ª troca.

Resultado: **OK** (ohaNon2xx ok · probe ok · writes ok · cutover ok · switches ok · staging ok · export ok)

## Resumo

| Métrica | Valor |
|---|---|
| Requisições oha (total) | 8004 |
| Respostas não-2xx (oha) | **0** ({"200":8000}) |
| Erros de conexão/tempo (oha) | **0** · + 4 cancelada(s) pelo próprio oha no fim do `-z` ("aborted due to deadline": em curso quando a carga acabou, não é resposta do hub) |
| Latência oha p50 / p95 / p99 / máx (ms) | 1.4 / 3.3 / 9.6 / 193.7 |
| RPS médio oha | 50.0 |
| Sonda (total / não-2xx / p99 ms) | 3871 / **0** / 28 |
| Exportação CSV longa | HTTP 200 · 300043 linhas · 80.5 MB · **concluída** em 25.0 s (atravessou 2 troca(s))  |
| Gravações POST (Idempotency-Key) | 1252 (626 cadastros + 626 repetições) · não-2xx **0** · repetições respondidas do registro (201 + idempotent-replay): **626/626** |
| Respostas por versão (sonda + gravações) | dev: 340 · 3.0.792606: 371 · 3.1.792606: 2851 · 3.2.792606: 309 |
| Respostas da versão de staging em produção | **0** |
| Staging durante a carga | Implantado; container de produção **intocado** (f4ad8b5560c9); staging /hub-app-ativos__staging running; sonda na janela: 118 req, 0 não-2xx, p99 19 ms |

## Trocas (janela da sonda: −2 s a +5 s em torno da troca de upstream)

| Ação | Resultado | Resposta da API | Duração (job) | Drenagem (ms) | Req. na janela | Não-2xx | p50 | p99 | máx |
|---|---|---|---|---|---|---|---|---|---|
| deploy 1 (3.0.792606) | ok | 3 ms | 0.9 s | 0 | 169 | 0 | 4 | 611 | 664 |
| deploy 2 (3.1.792606) | ok | 3 ms | 1.0 s | 0 | 179 | 0 | 5 | 32 | 43 |
| deploy 3 (3.2.792606) | ok | 2 ms | 1.1 s | 0 | 170 | 0 | 4 | 27 | 34 |
| rollback (3.2.792606 → anterior) | ok | 2 ms | 0.8 s | 0 | 167 | 0 | 3 | 28 | 31 |

## Virada por versão (`x-app-version`)

Por hora de início das requisições (sonda + gravações): primeira resposta da versão
nova, última da antiga e quantas respostas da antiga começaram **depois** da primeira
da nova (+150 ms de tolerância) — 0 = virada limpa, sem ida e volta.

| Troca | De → para | 1ª da nova (s) | Última da antiga (s) | Antiga depois da nova |
|---|---|---|---|---|
| deploy 1 (3.0.792606) | dev → 3.0.792606 | 53.03 | 52.97 | **0** |
| deploy 2 (3.1.792606) | 3.0.792606 → 3.1.792606 | 68.38 | 68.31 | **0** |
| deploy 3 (3.2.792606) | 3.1.792606 → 3.2.792606 | 83.99 | 83.93 | **0** |
| rollback (3.2.792606 → anterior) | 3.2.792606 → 3.1.792606 | 96.88 | 96.82 | **0** |

## Linha do tempo

- 38.9 s — versão atual em produção: dev · imagens: 3.0.792606=63fd5c5fa3ed, 3.1.792606=846a0fa2d6d8, 3.2.792606=a1381ff810b6, 4.0.792606=9672b664c90d
- 38.9 s — carga: oha 50 RPS por 160 s (fragmento + lista, via gateway) · sonda 20 RPS com carimbo de tempo
- 49.5 s — pacote 3.0.792606 validado (#6, Aguardando aprovação)
- 49.5 s — exportação CSV longa iniciada (leitura lenta, ~20 s)
- 53.4 s — deploy 1 (3.0.792606): ok em 0.9 s (202 em 3 ms) — Container hub-app-ativos substituído sem indisponibilidade (blue/green) · imagem sha256:63fd5c5fa3ed · health check ok · upstream http://hub-app-ativos:8080 · anterior drenado em 0 ms
- 67.9 s — pacote 3.1.792606 validado (#7, Aguardando aprovação)
- 68.9 s — deploy 2 (3.1.792606): ok em 1.0 s (202 em 3 ms) — Container hub-app-ativos substituído sem indisponibilidade (blue/green) · imagem sha256:846a0fa2d6d8 · health check ok · upstream http://hub-app-ativos:8080 · anterior drenado em 0 ms
- 83.5 s — pacote 3.2.792606 validado (#8, Aguardando aprovação)
- 84.5 s — deploy 3 (3.2.792606): ok em 1.1 s (202 em 2 ms) — Container hub-app-ativos substituído sem indisponibilidade (blue/green) · imagem sha256:a1381ff810b6 · health check ok · upstream http://hub-app-ativos:8080 · anterior drenado em 0 ms
- 97.3 s — rollback (3.2.792606 → anterior): ok em 0.8 s (202 em 2 ms) — Container hub-app-ativos restaurado do __prev (rollback instantâneo, sem indisponibilidade) · imagem sha256:846a0fa2d6d8 · health check ok · upstream http://hub-app-ativos:8080 · anterior drenado em 0 ms
- 109.6 s — banco de staging: HTTP 200 
- 112.8 s — staging 4.0.792606: Implantado — Container hub-app-ativos__staging criado · imagem sha256:9672b664c90d · health check ok · upstream http://hub-app-ativos__staging:8080
- 113.0 s — exportação: HTTP 200, 300043 linhas, 80.5 MB, concluída em 25.0 s 

## Detalhe do runtime

- **deploy 1 (3.0.792606)**: Container hub-app-ativos substituído sem indisponibilidade (blue/green) · imagem sha256:63fd5c5fa3ed · health check ok · upstream http://hub-app-ativos:8080 · anterior drenado em 0 ms
- **deploy 2 (3.1.792606)**: Container hub-app-ativos substituído sem indisponibilidade (blue/green) · imagem sha256:846a0fa2d6d8 · health check ok · upstream http://hub-app-ativos:8080 · anterior drenado em 0 ms
- **deploy 3 (3.2.792606)**: Container hub-app-ativos substituído sem indisponibilidade (blue/green) · imagem sha256:a1381ff810b6 · health check ok · upstream http://hub-app-ativos:8080 · anterior drenado em 0 ms
- **rollback (3.2.792606 → anterior)**: Container hub-app-ativos restaurado do __prev (rollback instantâneo, sem indisponibilidade) · imagem sha256:846a0fa2d6d8 · health check ok · upstream http://hub-app-ativos:8080 · anterior drenado em 0 ms
