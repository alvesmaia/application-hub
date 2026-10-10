# Implantação sem indisponibilidade sob carga — 2026-09-27

Gerado por `loadtest/zero-downtime/zero-downtime.mjs` (compose + `docker-compose.vm-destino.yml`).
Carga: `oha` a 200 RPS (c=64, `--latency-correction`) pelo gateway (90% fragmento
`/_frag/ativos/dashboard`, 10% lista `/_frag/ativos/assets?q=NT-1`), durante 160 s,
+ sonda própria a 20 RPS (75% fragmento, 25% lista; pela porta
publicada) com carimbo de tempo para medir as janelas das trocas, + uma exportação CSV do
`ativos` lida devagar (~20 s) começando antes da 1ª troca.

## Resumo

| Métrica | Valor |
|---|---|
| Requisições oha (total) | 32001 |
| Respostas não-2xx (oha) | **0** ({"200":32000}) |
| Erros de conexão/tempo (oha) | **0** · + 1 cancelada(s) pelo próprio oha no fim do `-z` ("aborted due to deadline": em curso quando a carga acabou, não é resposta do hub) |
| Latência oha p50 / p95 / p99 / máx (ms) | 1.3 / 7.9 / 74.6 / 243.5 |
| RPS médio oha | 200.0 |
| Sonda (total / não-2xx / p99 ms) | 3873 / **0** / 105 |
| Exportação CSV longa | HTTP 200 · 1000001 linhas · 274.1 MB · **concluída** em 88.8 s (atravessou 4 troca(s))  |
| Gravações POST (Idempotency-Key) | 1244 (622 cadastros + 622 repetições) · não-2xx **0** · repetições respondidas do registro (201 + idempotent-replay): **622/622** |
| Respostas por versão (sonda + gravações) | 3.1.577310: 340 · 3.0.764197: 1214 · 3.1.764197: 1989 · 3.2.764197: 330 |
| Respostas da versão de staging em produção | **0** |
| Staging durante a carga | Implantado; container de produção **intocado** (be969b61f24e); staging /hub-app-ativos__staging running; sonda na janela: 135 req, 0 não-2xx, p99 23 ms |

## Trocas (janela da sonda: −2 s a +5 s em torno da troca de upstream)

| Ação | Resultado | Resposta da API | Duração (job) | Drenagem (ms) | Req. na janela | Não-2xx | p50 | p99 | máx |
|---|---|---|---|---|---|---|---|---|---|
| deploy 1 (3.0.764197) | ok | 2 ms | 36.4 s | 35163 | 169 | 0 | 5 | 167 | 169 |
| deploy 2 (3.1.764197) | ok | 32 ms | 1.3 s | 26 | 167 | 0 | 29 | 81 | 233 |
| deploy 3 (3.2.764197) | ok | 5 ms | 1.2 s | 26 | 174 | 0 | 21 | 48 | 74 |
| rollback (3.2.764197 → anterior) | ok | 11 ms | 0.9 s | 0 | 184 | 0 | 12 | 43 | 75 |

## Virada por versão (`x-app-version`)

Por hora de início das requisições (sonda + gravações): primeira resposta da versão
nova, última da antiga e quantas respostas da antiga começaram **depois** da primeira
da nova (+150 ms de tolerância) — 0 = virada limpa, sem ida e volta.

| Troca | De → para | 1ª da nova (s) | Última da antiga (s) | Antiga depois da nova |
|---|---|---|---|---|
| deploy 1 (3.0.764197) | 3.1.577310 → 3.0.764197 | 24.32 | 24.25 | **0** |
| deploy 2 (3.1.764197) | 3.0.764197 → 3.1.764197 | 75.34 | 75.31 | **0** |
| deploy 3 (3.2.764197) | 3.1.764197 → 3.2.764197 | 91.31 | 91.25 | **0** |
| rollback (3.2.764197 → anterior) | 3.2.764197 → 3.1.764197 | 104.28 | 104.24 | **0** |

## Linha do tempo

- 10.1 s — versão atual em produção: 3.1.577310 · imagens: 3.0.764197=1110dca62d62, 3.1.764197=41234f2d3aab, 3.2.764197=32ced349d6cb, 4.0.764197=0f1ef6a8ac80
- 10.1 s — carga: oha 200 RPS por 160 s (fragmento + lista, via gateway) · sonda 20 RPS com carimbo de tempo
- 20.7 s — pacote 3.0.764197 validado (#33, Aguardando aprovação)
- 20.7 s — exportação CSV longa iniciada (leitura lenta, ~20 s)
- 60.1 s — deploy 1 (3.0.764197): ok em 36.4 s (202 em 2 ms) — Container hub-app-ativos substituído sem indisponibilidade (blue/green) · imagem sha256:1110dca62d62 · health check ok · upstream http://hub-app-ativos:8080 · anterior drenado em 35163 ms
- 74.7 s — pacote 3.1.764197 validado (#34, Aguardando aprovação)
- 76.1 s — deploy 2 (3.1.764197): ok em 1.3 s (202 em 32 ms) — Container hub-app-ativos substituído sem indisponibilidade (blue/green) · imagem sha256:41234f2d3aab · health check ok · upstream http://hub-app-ativos:8080 · anterior drenado em 26 ms
- 90.7 s — pacote 3.2.764197 validado (#35, Aguardando aprovação)
- 91.9 s — deploy 3 (3.2.764197): ok em 1.2 s (202 em 5 ms) — Container hub-app-ativos substituído sem indisponibilidade (blue/green) · imagem sha256:32ced349d6cb · health check ok · upstream http://hub-app-ativos:8080 · anterior drenado em 26 ms
- 104.8 s — rollback (3.2.764197 → anterior): ok em 0.9 s (202 em 11 ms) — Container hub-app-ativos restaurado do __prev (rollback instantâneo, sem indisponibilidade) · imagem sha256:41234f2d3aab · health check ok · upstream http://hub-app-ativos:8080 · anterior drenado em 0 ms
- 117.2 s — banco de staging: HTTP 200 
- 120.9 s — staging 4.0.764197: Implantado — Container hub-app-ativos__staging substituído sem indisponibilidade (blue/green) · imagem sha256:0f1ef6a8ac80 · health check ok · upstream http://hub-app-ativos__staging:8080 · anterior drenado em 0 ms
- 121.1 s — exportação: HTTP 200, 1000001 linhas, 274.1 MB, concluída em 88.8 s 

## Detalhe do runtime

- **deploy 1 (3.0.764197)**: Container hub-app-ativos substituído sem indisponibilidade (blue/green) · imagem sha256:1110dca62d62 · health check ok · upstream http://hub-app-ativos:8080 · anterior drenado em 35163 ms
- **deploy 2 (3.1.764197)**: Container hub-app-ativos substituído sem indisponibilidade (blue/green) · imagem sha256:41234f2d3aab · health check ok · upstream http://hub-app-ativos:8080 · anterior drenado em 26 ms
- **deploy 3 (3.2.764197)**: Container hub-app-ativos substituído sem indisponibilidade (blue/green) · imagem sha256:32ced349d6cb · health check ok · upstream http://hub-app-ativos:8080 · anterior drenado em 26 ms
- **rollback (3.2.764197 → anterior)**: Container hub-app-ativos restaurado do __prev (rollback instantâneo, sem indisponibilidade) · imagem sha256:41234f2d3aab · health check ok · upstream http://hub-app-ativos:8080 · anterior drenado em 0 ms
