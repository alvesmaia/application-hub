# Carga realista — APP_MEM=1g, 1 vCPU por app (VM de destino) · contratos: CT_MEM=1g, UVICORN_WORKERS=2, HUB_JOBS_MODE=process — 2026-09-27T21:06:53.128Z

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
| contratos-u10 | ✅ | 3 | 217.7 | 23.2 | 61.5 | 3034.5 | 4122.5 | — | 2.1 | 0 | 0 | 6.2 | 14% | 160MiB | 1% | 186 MiB | 15% | 30 |
| contratos-u15 | ✅ | 4.2 | 273.1 | 40.1 | 69.5 | 5129.1 | 7338.5 | — | 5.1 | 0 | 0 | 6.5 | 33% | 175MiB | 1% | 187 MiB | 19% | 35 |
| contratos-u20 | ✅ | 6.1 | 237.3 | 39.3 | 72.3 | 9236.5 | 9099.4 | — | 3.2 | 0 | 0 | 6.6 | 38% | 190MiB | 2% | 187 MiB | 19% | 37 |
| contratos-u25 | ✅ | 7.6 | 231.8 | 35.4 | 66.1 | 4125.3 | 5322.6 | — | 3.1 | 0 | 0 | 4.8 | 15% | 195MiB | 2% | 186 MiB | 21% | 37 |
| contratos-u30 | ✅ | 9.1 | 273.9 | 62.9 | 75.9 | 3138.9 | 7231.9 | — | 2.1 | 0 | 0 | 7.2 | 19% | 215MiB | 2% | 186 MiB | 32% | 37 |
| contratos-u40 | ✅ | 11.7 | 248.1 | 49.2 | 75.8 | 6181.9 | 10241.2 | — | 4.2 | 0 | 0 | 5 | 43% | 238MiB | 2% | 187 MiB | 49% | 39 |
| contratos-u50 | ✅ | 12.1 | 262.7 | 47.4 | 77.6 | 18645.8 | 20269.8 | — | 18.8 | 0 | 0 | 6.2 | 60% | 246MiB | 3% | 187 MiB | 42% | 41 |
| contratos-u75 | ✅ | 17.3 | 406 | 87.6 | 87.6 | 28608.5 | 31059.6 | — | 29.1 | 0 | 0 | 7.1 | 71% | 263MiB | 3% | 187 MiB | 64% | 43 |
| contratos-u100 | ✅ | 21.3 | 419.4 | 108.7 | 139.2 | 41525.3 | 46404.3 | — | 26.0 | 0 | 0 | 8.5 | 68% | 274MiB | 5% | 186 MiB | 68% | 52 |

Banco: postgres=7519 kB, ativos=310 MB, servicos=929 MB, app_contratos=424 MB · object store: 184.0K	/data

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
