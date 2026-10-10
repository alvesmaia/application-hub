# Carga realista — APP_MEM=1g, 1 vCPU por app (VM de destino) · contratos: CT_MEM=1g, UVICORN_WORKERS=1, HUB_JOBS_MODE=process — 2026-09-27T21:12:16.562Z

Gerado por `loadtest/realistic.mjs run --mem 1g`. VUs com 2–6 s de pausa entre ações; mistura:
60% lista/busca · 20% detalhe/edição (Idempotency-Key; 409 = negócio) · 8% exportação ·
5% relatório pesado · 5% anexo · 2% importação (5 mil linhas, ≤ 2 simultâneas). Passos de
45 s medidos (+10 s de aquecimento). SLO: p95 < 500 ms em lista e detalhe, erros < 1%,
nenhum OOM/reinício. Respostas 429 (semáforo de operações pesadas do app, proteção por projeto) contam à parte
(coluna 429 %), não como erro; 422/409 de edição são respostas de negócio. Sonda do hub: 5 RPS em `/applications`.

## Máximo de usuários simultâneos dentro do SLO

| Cenário | Usuários (máx. no SLO) | Quebrou em | Motivo |
|---|---|---|---|
| mix | **50** | 75 | lista p95 541.5 ms |

## Passos

| Passo | SLO | req/s | lista p95 | detalhe p95 | edição p95 | export p95 | pesado p95 | anexo p95 | import (s) | erros % | 429 % | hub p99 | CPU app (média) | mem app (pico) | CPU hub | mem hub | CPU db | conexões db |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| mix-u10 | ✅ | 2.9 | 176.3 | 99.5 | 319.9 | 3252.5 | 1188.3 | 47.1 | — | 0 | 0 | 5.5 | 2%/0%/0%/2% | 29MiB/3MiB/2MiB/79MiB | 1% | 186 MiB | 15% | 30 |
| mix-u25 | ✅ | 7.2 | 161.8 | 101.9 | 370.8 | 2119.6 | 4225.4 | 23.9 | 1.0 | 0 | 0 | 6.8 | 2%/0%/0%/9% | 39MiB/4MiB/2MiB/99MiB | 1% | 186 MiB | 24% | 34 |
| mix-u50 | ✅ | 13.9 | 295.4 | 132.3 | 739.5 | 2115.3 | 5153.2 | 24 | 1.1 | 0 | 0 | 6.4 | 3%/0%/0%/28% | 41MiB/4MiB/2MiB/112MiB | 2% | 187 MiB | 62% | 36 |
| mix-u75 | ❌ | 20.2 | 541.5 | 308.3 | 2797.5 | 1317 | 5449.3 | 43.6 | 1.0 | 0 | 0.11 | 13.1 | 7%/0%/0%/32% | 41MiB/5MiB/2MiB/117MiB | 4% | 187 MiB | 94% | 43 |

Banco: postgres=7519 kB, ativos=310 MB, servicos=929 MB, app_contratos=431 MB · object store: 13.2M	/data

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
