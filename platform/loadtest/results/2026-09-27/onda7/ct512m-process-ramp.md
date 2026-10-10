# Carga realista — APP_MEM=1g, 1 vCPU por app (VM de destino) · contratos: CT_MEM=512m, UVICORN_WORKERS=1, HUB_JOBS_MODE=process — 2026-09-27T21:18:46.494Z

Gerado por `loadtest/realistic.mjs run --mem 1g`. VUs com 2–6 s de pausa entre ações; mistura:
60% lista/busca · 20% detalhe/edição (Idempotency-Key; 409 = negócio) · 8% exportação ·
5% relatório pesado · 5% anexo · 2% importação (5 mil linhas, ≤ 2 simultâneas). Passos de
45 s medidos (+10 s de aquecimento). SLO: p95 < 500 ms em lista e detalhe, erros < 1%,
nenhum OOM/reinício. Respostas 429 (semáforo de operações pesadas do app, proteção por projeto) contam à parte
(coluna 429 %), não como erro; 422/409 de edição são respostas de negócio. Sonda do hub: 5 RPS em `/applications`.

## Máximo de usuários simultâneos dentro do SLO

| Cenário | Usuários (máx. no SLO) | Quebrou em | Motivo |
|---|---|---|---|
| contratos | **100** | 150 | lista p95 1143.6 ms · detalhe p95 1054.7 ms |

## Passos

| Passo | SLO | req/s | lista p95 | detalhe p95 | edição p95 | export p95 | pesado p95 | anexo p95 | import (s) | erros % | 429 % | hub p99 | CPU app (média) | mem app (pico) | CPU hub | mem hub | CPU db | conexões db |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| contratos-u100 | ✅ | 19.2 | 349.5 | 84.7 | 127.7 | 48947.2 | 47056.8 | — | 36.8 | 0 | 0 | 7.1 | 75% | 119MiB | 4% | 207 MiB | 66% | 55 |
| contratos-u150 | ❌ | 25.2 | 1143.6 | 1054.7 | 1277.8 | 61628.9 | 66693.5 | — | 27.1 | 0 | 0.09 | 8.5 | 72% | 137MiB | 6% | 208 MiB | 97% | 60 |

Banco: postgres=7519 kB, ativos=310 MB, servicos=929 MB, app_contratos=452 MB · object store: 13.3M	/data

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
