# Carga realista — APP_MEM=1g, 1 vCPU por app (VM de destino) · contratos: CT_MEM=1g, UVICORN_WORKERS=1, HUB_JOBS_MODE=process — 2026-09-27T20:25:00.637Z

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
| contratos-u10 | ✅ | 3 | 214.2 | 21.5 | 66.3 | 5079.7 | 5140.2 | — | 3.0 | 0 | 0 | 6.1 | 23% | 95MiB | 1% | 117 MiB | 16% | 14 |
| contratos-u15 | ✅ | 4.4 | 229.7 | 24.5 | 75.9 | 2088.7 | 4060.3 | — | 3.0 | 0 | 0 | 4.8 | 11% | 97MiB | 1% | 117 MiB | 14% | 15 |
| contratos-u20 | ✅ | 6.1 | 228.5 | 35.5 | 72.3 | 3085.2 | 4088.1 | — | 2.1 | 0 | 0 | 5.7 | 13% | 111MiB | 1% | 117 MiB | 21% | 15 |
| contratos-u25 | ✅ | 7.3 | 233 | 59.2 | 92.3 | 4175.8 | 5210.2 | — | 3.1 | 0 | 0 | 5.7 | 37% | 126MiB | 2% | 117 MiB | 27% | 16 |
| contratos-u30 | ✅ | 8.8 | 213 | 25.4 | 71.3 | 7108.6 | 10223 | — | 4.1 | 0 | 0 | 5.1 | 44% | 148MiB | 2% | 117 MiB | 26% | 17 |
| contratos-u40 | ✅ | 10.4 | 219.8 | 28 | 79.8 | 16272.1 | 15212.8 | — | 10.2 | 0 | 0 | 4.8 | 60% | 147MiB | 2% | 117 MiB | 24% | 18 |
| contratos-u50 | ✅ | 13.4 | 213.7 | 35.5 | 70.4 | 19257.3 | 20224.9 | — | 7.1 | 0 | 0 | 4.9 | 54% | 166MiB | 3% | 118 MiB | 20% | 19 |
| contratos-u75 | ✅ | 15.7 | 392.5 | 87.2 | 90 | 37757.2 | 39424.1 | — | 33.6 | 0 | 0 | 5.7 | 59% | 168MiB | 3% | 119 MiB | 37% | 26 |
| contratos-u100 | ✅ | 22 | 416.9 | 99 | 119.5 | 37619.8 | 38664.1 | — | 23.6 | 0 | 0 | 6.8 | 56% | 194MiB | 4% | 120 MiB | 62% | 31 |

Banco: postgres=7519 kB, ativos=303 MB, servicos=929 MB, app_contratos=262 MB · object store: 376.0K	/data

HostConfig efetivo:

```
hub-web cpus=1000000000 mem=1073741824 cpuset=0-3
hub-app-ativos cpus=1000000000 mem=1073741824 cpuset=0-3
hub-app-fornecedores cpus=1000000000 mem=1073741824 cpuset=0-3
hub-app-servicos cpus=1000000000 mem=1073741824 cpuset=0-3
hub-app-contratos cpus=1000000000 mem=1073741824 cpuset=0-3
hub-db cpus=2000000000 mem=1073741824 cpuset=0-3
hub-objstore cpus=500000000 mem=536870912 cpuset=0-3
hub-extapi cpus=250000000 mem=67108864 cpuset=0-3
```
