# Carga realista — APP_MEM=1g, 1 vCPU por app (VM de destino) · contratos: CT_MEM=512m, UVICORN_WORKERS=1, HUB_JOBS_MODE=process — 2026-09-27T20:54:22.974Z

Gerado por `loadtest/realistic.mjs run --mem 1g`. VUs com 2–6 s de pausa entre ações; mistura:
60% lista/busca · 20% detalhe/edição (Idempotency-Key; 409 = negócio) · 8% exportação ·
5% relatório pesado · 5% anexo · 2% importação (5 mil linhas, ≤ 2 simultâneas). Passos de
45 s medidos (+10 s de aquecimento). SLO: p95 < 500 ms em lista e detalhe, erros < 1%,
nenhum OOM/reinício. Respostas 429 (semáforo de operações pesadas do app, proteção por projeto) contam à parte
(coluna 429 %), não como erro; 422/409 de edição são respostas de negócio. Sonda do hub: 5 RPS em `/applications`.

## Máximo de usuários simultâneos dentro do SLO

| Cenário | Usuários (máx. no SLO) | Quebrou em | Motivo |
|---|---|---|---|
| contratos | **100** | — | não quebrou na rampa |

## Passos

| Passo | SLO | req/s | lista p95 | detalhe p95 | edição p95 | export p95 | pesado p95 | anexo p95 | import (s) | erros % | 429 % | hub p99 | CPU app (média) | mem app (pico) | CPU hub | mem hub | CPU db | conexões db |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| contratos-u10 | ✅ | 3 | 232.4 | 24.4 | 74.6 | 2505 | 3168.9 | — | 2.7 | 0 | 0 | 4.5 | 13% | 96MiB | 1% | 163 MiB | 15% | 30 |
| contratos-u15 | ✅ | 4.9 | 240.2 | 22.7 | 68.6 | 5123.5 | 6184.8 | — | 2.0 | 0 | 0 | 4.7 | 32% | 105MiB | 2% | 163 MiB | 19% | 33 |
| contratos-u20 | ✅ | 6.1 | 224.9 | 25.7 | 68.7 | 3043.1 | 4131.3 | — | 2.1 | 0 | 0 | 5.6 | 10% | 126MiB | 1% | 163 MiB | 21% | 33 |
| contratos-u25 | ✅ | 7.5 | 235.1 | 31.3 | 68.3 | 5094 | 8110.2 | — | 2.1 | 0 | 0 | 7.2 | 31% | 135MiB | 1% | 163 MiB | 33% | 33 |
| contratos-u30 | ✅ | 8.9 | 265.8 | 28.1 | 74.2 | 3120 | 7074.9 | — | 3.1 | 0 | 0 | 4.4 | 32% | 136MiB | 2% | 163 MiB | 35% | 33 |
| contratos-u40 | ✅ | 11.4 | 263.4 | 32.7 | 80.3 | 9127.6 | 10146 | — | 4.1 | 0 | 0 | 5 | 47% | 159MiB | 2% | 164 MiB | 40% | 35 |
| contratos-u50 | ✅ | 13 | 284.8 | 52.9 | 81.3 | 22307.1 | 22291.9 | — | 10.2 | 0 | 0 | 5.6 | 44% | 163MiB | 3% | 163 MiB | 38% | 37 |
| contratos-u75 | ✅ | 17.4 | 293.9 | 67.1 | 69.2 | 27368.3 | 30269.8 | — | 19.4 | 0 | 0 | 6.6 | 35% | 175MiB | 3% | 163 MiB | 44% | 37 |
| contratos-u100 | ✅ | 20.1 | 493.8 | 239.9 | 119 | 48071.1 | 53113.8 | — | 18.7 | 0 | 0 | 7.7 | 60% | 195MiB | 4% | 167 MiB | 57% | 46 |

Banco: postgres=7519 kB, ativos=310 MB, servicos=929 MB, app_contratos=359 MB · object store: 328.0K	/data

HostConfig efetivo:

```
hub-web cpus=1000000000 mem=1073741824 cpuset=0-3
hub-app-ativos cpus=1000000000 mem=1073741824 cpuset=0-3
hub-app-fornecedores cpus=1000000000 mem=1073741824 cpuset=0-3
hub-app-servicos cpus=1000000000 mem=1073741824 cpuset=0-3
hub-app-contratos cpus=1000000000 mem=536870912 cpuset=0-3
hub-db cpus=2000000000 mem=1073741824 cpuset=0-3
hub-objstore cpus=500000000 mem=536870912 cpuset=0-3
hub-extapi cpus=250000000 mem=67108864 cpuset=0-3
```
