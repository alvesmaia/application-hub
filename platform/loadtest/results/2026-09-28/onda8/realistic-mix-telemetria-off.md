# Carga realista — APP_MEM=1g, 1 vCPU por app (VM de destino) · contratos: CT_MEM=1g, UVICORN_WORKERS=1, HUB_JOBS_MODE=process — 2026-09-28T03:48:09.727Z

Gerado por `loadtest/realistic.mjs run --mem 1g`. VUs com 2–6 s de pausa entre ações; mistura:
60% lista/busca · 20% detalhe/edição (Idempotency-Key; 409 = negócio) · 8% exportação ·
5% relatório pesado · 5% anexo · 2% importação (5 mil linhas, ≤ 2 simultâneas). Passos de
45 s medidos (+10 s de aquecimento). SLO: p95 < 500 ms em lista e detalhe, erros < 1%,
nenhum OOM/reinício. Respostas 429 (semáforo de operações pesadas do app, proteção por projeto) contam à parte
(coluna 429 %), não como erro; 422/409 de edição são respostas de negócio. Sonda do hub: 5 RPS em `/applications`.

## Máximo de usuários simultâneos dentro do SLO

| Cenário | Usuários (máx. no SLO) | Quebrou em | Motivo |
|---|---|---|---|
| mix | **50** | 75 | lista p95 938.9 ms · detalhe p95 846.6 ms |

## Passos

| Passo | SLO | req/s | lista p95 | detalhe p95 | edição p95 | export p95 | pesado p95 | anexo p95 | import (s) | erros % | 429 % | hub p99 | CPU app (média) | mem app (pico) | CPU hub | mem hub | CPU db | conexões db |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| mix-u25 | ✅ | 6.6 | 262.6 | 210.9 | 1441.7 | 3080.5 | 3093.2 | 32.7 | — | 0 | 0 | 4.7 | 3%/0%/1%/8% | 16MiB/4MiB/3MiB/116MiB | 1% | 47 MiB | 53% | 35 |
| mix-u50 | ✅ | 12.7 | 312.8 | 368.1 | 356.1 | 21357.2 | 44767.9 | 30.1 | 1.1 | 0 | 0.35 | 46.5 | 6%/0%/0%/88% | 30MiB/4MiB/3MiB/127MiB | 3% | 50 MiB | 62% | 35 |
| mix-u75 | ❌ | 19.7 | 938.9 | 846.6 | 360.5 | 2225.2 | 6494.6 | 100.9 | 1.1 | 0 | 0.34 | 39.3 | 12%/0%/0%/66% | 48MiB/6MiB/4MiB/142MiB | 5% | 53 MiB | 110% | 40 |

Banco: postgres=7519 kB, servicos=929 MB, ativos=541 MB, app_contratos=1568 MB · object store: 24.5M	/data

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
