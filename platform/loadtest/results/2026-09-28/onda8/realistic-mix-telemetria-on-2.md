# Carga realista — APP_MEM=1g, 1 vCPU por app (VM de destino) · contratos: CT_MEM=1g, UVICORN_WORKERS=1, HUB_JOBS_MODE=process — 2026-09-28T03:55:51.541Z

Gerado por `loadtest/realistic.mjs run --mem 1g`. VUs com 2–6 s de pausa entre ações; mistura:
60% lista/busca · 20% detalhe/edição (Idempotency-Key; 409 = negócio) · 8% exportação ·
5% relatório pesado · 5% anexo · 2% importação (5 mil linhas, ≤ 2 simultâneas). Passos de
45 s medidos (+10 s de aquecimento). SLO: p95 < 500 ms em lista e detalhe, erros < 1%,
nenhum OOM/reinício. Respostas 429 (semáforo de operações pesadas do app, proteção por projeto) contam à parte
(coluna 429 %), não como erro; 422/409 de edição são respostas de negócio. Sonda do hub: 5 RPS em `/applications`.

## Máximo de usuários simultâneos dentro do SLO

| Cenário | Usuários (máx. no SLO) | Quebrou em | Motivo |
|---|---|---|---|
| mix | **50** | 75 | lista p95 935.1 ms · detalhe p95 657.4 ms |

## Passos

| Passo | SLO | req/s | lista p95 | detalhe p95 | edição p95 | export p95 | pesado p95 | anexo p95 | import (s) | erros % | 429 % | hub p99 | CPU app (média) | mem app (pico) | CPU hub | mem hub | CPU db | conexões db |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| mix-u50 | ✅ | 13.3 | 366.1 | 452.8 | 1996.3 | 2026.5 | 4243.7 | 52.9 | 1.1 | 0 | 0.17 | 26.4 | 3%/0%/0%/62% | 29MiB/6MiB/5MiB/146MiB | 3% | 62 MiB | 76% | 35 |
| mix-u75 | ❌ | 20.1 | 935.1 | 657.4 | 911.7 | 3077.5 | 6412 | 29.2 | 1.1 | 0 | 0.44 | 50.7 | 14%/1%/1%/62% | 55MiB/7MiB/5MiB/151MiB | 6% | 69 MiB | 145% | 38 |

Banco: postgres=7519 kB, servicos=929 MB, ativos=541 MB, app_contratos=1575 MB · object store: 31.4M	/data

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
