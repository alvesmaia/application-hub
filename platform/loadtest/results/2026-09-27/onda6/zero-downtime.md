# Implantação sem indisponibilidade sob carga — 2026-09-27

Gerado por `loadtest/zero-downtime.mjs` (compose no ar; o override VM de destino é opcional).
Carga: `oha` a 200 RPS (c=64, `--latency-correction`) pelo gateway (90% fragmento
`/_frag/ativos/dashboard`, 10% lista `/_frag/ativos/assets?q=NT-1`), durante 160 s,
+ sonda própria a 20 RPS (75% fragmento, 25% lista; pela porta
publicada) com carimbo de tempo para medir as janelas das trocas, + uma exportação CSV do
`ativos` lida devagar (~20 s) começando antes da 1ª troca.

Resultado: **OK** (ohaNon2xx ok · probe ok · writes ok · cutover ok · switches ok · staging ok · export ok)

## Resumo

| Métrica | Valor |
|---|---|
| Requisições oha (total) | 32001 |
| Respostas não-2xx (oha) | **0** ({"200":32000}) |
| Erros de conexão/tempo (oha) | **0** · + 1 cancelada(s) pelo próprio oha no fim do `-z` ("aborted due to deadline": em curso quando a carga acabou, não é resposta do hub) |
| Latência oha p50 / p95 / p99 / máx (ms) | 1.2 / 3.0 / 12.7 / 139.0 |
| RPS médio oha | 200.0 |
| Sonda (total / não-2xx / p99 ms) | 3890 / **0** / 35 |
| Exportação CSV longa | HTTP 200 · 498096 linhas · 137.4 MB · **concluída** em 33.6 s (atravessou 2 troca(s))  |
| Gravações POST (Idempotency-Key) | 1254 (627 cadastros + 627 repetições) · não-2xx **0** · repetições respondidas do registro (201 + idempotent-replay): **627/627** |
| Respostas por versão (sonda + gravações) | dev: 343 · 3.0.569768: 702 · 3.1.569768: 2535 · 3.2.569768: 310 |
| Respostas da versão de staging em produção | **0** |
| Staging durante a carga | Implantado; container de produção **intocado** (089279d34e2f); staging /hub-app-ativos__staging running; sonda na janela: 121 req, 0 não-2xx, p99 27 ms |

## Trocas (janela da sonda: −2 s a +5 s em torno da troca de upstream)

| Ação | Resultado | Resposta da API | Duração (job) | Drenagem (ms) | Req. na janela | Não-2xx | p50 | p99 | máx |
|---|---|---|---|---|---|---|---|---|---|
| deploy 1 (3.0.569768) | ok | 64 ms | 13.7 s | 12575 | 170 | 0 | 4 | 633 | 673 |
| deploy 2 (3.1.569768) | ok | 7 ms | 1.1 s | 0 | 179 | 0 | 5 | 35 | 45 |
| deploy 3 (3.2.569768) | ok | 2 ms | 1.1 s | 26 | 168 | 0 | 3 | 30 | 31 |
| rollback (3.2.569768 → anterior) | ok | 2 ms | 0.8 s | 0 | 168 | 0 | 3 | 28 | 33 |

## Virada por versão (`x-app-version`)

Por hora de início das requisições (sonda + gravações): primeira resposta da versão
nova, última da antiga e quantas respostas da antiga começaram **depois** da primeira
da nova (+150 ms de tolerância) — 0 = virada limpa, sem ida e volta.

| Troca | De → para | 1ª da nova (s) | Última da antiga (s) | Antiga depois da nova |
|---|---|---|---|---|
| deploy 1 (3.0.569768) | dev → 3.0.569768 | 63.85 | 63.79 | **0** |
| deploy 2 (3.1.569768) | 3.0.569768 → 3.1.569768 | 91.91 | 91.86 | **0** |
| deploy 3 (3.2.569768) | 3.1.569768 → 3.2.569768 | 107.49 | 107.48 | **0** |
| rollback (3.2.569768 → anterior) | 3.2.569768 → 3.1.569768 | 120.48 | 120.42 | **0** |

## Linha do tempo

- 49.5 s — versão atual em produção: dev · imagens: 3.0.569768=a978d7780146, 3.1.569768=91526962fd75, 3.2.569768=5a26c541f18e, 4.0.569768=dde9cd67ea8c
- 49.5 s — carga: oha 200 RPS por 160 s (fragmento + lista, via gateway) · sonda 20 RPS com carimbo de tempo
- 60.1 s — pacote 3.0.569768 validado (#10, Aguardando aprovação)
- 60.1 s — exportação CSV longa iniciada (leitura lenta, ~20 s)
- 76.8 s — deploy 1 (3.0.569768): ok em 13.7 s (202 em 64 ms) — Container hub-app-ativos substituído sem indisponibilidade (blue/green) · imagem sha256:a978d7780146 · health check ok · upstream http://hub-app-ativos:8080 · anterior drenado em 12575 ms
- 91.4 s — pacote 3.1.569768 validado (#11, Aguardando aprovação)
- 92.5 s — deploy 2 (3.1.569768): ok em 1.1 s (202 em 7 ms) — Container hub-app-ativos substituído sem indisponibilidade (blue/green) · imagem sha256:91526962fd75 · health check ok · upstream http://hub-app-ativos:8080 · anterior drenado em 0 ms
- 107.0 s — pacote 3.2.569768 validado (#12, Aguardando aprovação)
- 108.1 s — deploy 3 (3.2.569768): ok em 1.1 s (202 em 2 ms) — Container hub-app-ativos substituído sem indisponibilidade (blue/green) · imagem sha256:5a26c541f18e · health check ok · upstream http://hub-app-ativos:8080 · anterior drenado em 26 ms
- 120.9 s — rollback (3.2.569768 → anterior): ok em 0.8 s (202 em 2 ms) — Container hub-app-ativos restaurado do __prev (rollback instantâneo, sem indisponibilidade) · imagem sha256:91526962fd75 · health check ok · upstream http://hub-app-ativos:8080 · anterior drenado em 0 ms
- 133.2 s — banco de staging: HTTP 200 
- 136.2 s — staging 4.0.569768: Implantado — Container hub-app-ativos__staging criado · imagem sha256:dde9cd67ea8c · health check ok · upstream http://hub-app-ativos__staging:8080
- 136.4 s — exportação: HTTP 200, 498096 linhas, 137.4 MB, concluída em 33.6 s 

## Detalhe do runtime

- **deploy 1 (3.0.569768)**: Container hub-app-ativos substituído sem indisponibilidade (blue/green) · imagem sha256:a978d7780146 · health check ok · upstream http://hub-app-ativos:8080 · anterior drenado em 12575 ms
- **deploy 2 (3.1.569768)**: Container hub-app-ativos substituído sem indisponibilidade (blue/green) · imagem sha256:91526962fd75 · health check ok · upstream http://hub-app-ativos:8080 · anterior drenado em 0 ms
- **deploy 3 (3.2.569768)**: Container hub-app-ativos substituído sem indisponibilidade (blue/green) · imagem sha256:5a26c541f18e · health check ok · upstream http://hub-app-ativos:8080 · anterior drenado em 26 ms
- **rollback (3.2.569768 → anterior)**: Container hub-app-ativos restaurado do __prev (rollback instantâneo, sem indisponibilidade) · imagem sha256:91526962fd75 · health check ok · upstream http://hub-app-ativos:8080 · anterior drenado em 0 ms
