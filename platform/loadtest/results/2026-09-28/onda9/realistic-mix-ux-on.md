# Carga realista — APP_MEM=1g, 1 vCPU por app (VM de destino) · contratos: CT_MEM=1g, UVICORN_WORKERS=1, HUB_JOBS_MODE=process — 2026-09-28T07:30:18.414Z

Gerado por `loadtest/realistic.mjs run --mem 1g`. VUs com 2–6 s de pausa entre ações; mistura:
60% lista/busca · 20% detalhe/edição (Idempotency-Key; 409 = negócio) · 8% exportação ·
5% relatório pesado · 5% anexo · 2% importação (5 mil linhas, ≤ 2 simultâneas). Passos de
45 s medidos (+10 s de aquecimento). SLO: p95 < 500 ms em lista e detalhe, erros < 1%,
nenhum OOM/reinício. Respostas 429 (semáforo de operações pesadas do app, proteção por projeto) contam à parte
(coluna 429 %), não como erro; 422/409 de edição são respostas de negócio. Sonda do hub: 5 RPS em `/applications`.

## Máximo de usuários simultâneos dentro do SLO

| Cenário | Usuários (máx. no SLO) | Quebrou em | Motivo |
|---|---|---|---|
| mix | **50** | — | não quebrou na rampa |

## Passos

| Passo | SLO | req/s | lista p95 | detalhe p95 | edição p95 | export p95 | pesado p95 | anexo p95 | import (s) | erros % | 429 % | hub p99 | CPU app (média) | mem app (pico) | CPU hub | mem hub | CPU db | conexões db |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| mix-u25 | ✅ | 7.1 | 284.9 | 89.2 | 2102.3 | 2109.2 | 3129.4 | 36.8 | 1.0 | 0 | 0 | 8.6 | 3%/0%/0%/5% | 15MiB/3MiB/3MiB/87MiB | 2% | 100 MiB | 44% | 21 |
| mix-u50 | ✅ | 14.3 | 272.5 | 109.8 | 1893.8 | 2099 | 3201 | 33.9 | 1.1 | 0 | 0 | 18.4 | 3%/0%/1%/16% | 18MiB/4MiB/2MiB/94MiB | 3% | 103 MiB | 74% | 23 |

Banco: postgres=7519 kB, ativos=309 MB, servicos=929 MB, app_contratos=198 MB · object store: 5.2M	/data

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
